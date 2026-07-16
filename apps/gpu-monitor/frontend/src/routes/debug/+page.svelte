<script lang="ts">
import { onMount } from 'svelte';

import { activeDevScenario, resetDevScenario, setDevScenario } from '$lib/stores/devScenario';
import { normalizeServerState } from '$lib/stores/servers';
import type { ServerRecord, ServerState } from '$lib/types';
import { mergeServerRecordState } from '$lib/utils/serverStateMerge';
import { DEV_SCENARIOS, applyDevScenario, type DevScenario } from '$lib/utils/devScenario';

type StatusMap = Record<string, unknown>;

const devMode = import.meta.env.DEV;
const scenarioMeta: Record<DevScenario, { title: string; summary: string }> = {
	normal: {
		title: 'normal',
		summary: '실측 개발 스냅샷 그대로 표시합니다.'
	},
	stale: {
		title: 'stale',
		summary: '첫 서버를 약 90초 지연된 텔레메트리로 보이게 합니다.'
	},
	io: {
		title: 'io',
		summary: '첫 서버에 PSI I/O 병목을 주입합니다.'
	},
	offline: {
		title: 'offline',
		summary: '첫 서버를 SSH timeout 오프라인 스냅샷으로 전환합니다.'
	},
	gpu_missing: {
		title: 'gpu_missing',
		summary: '첫 서버를 GPU 누락(degraded) 스냅샷으로 전환합니다.'
	},
	mixed: {
		title: 'mixed',
		summary: '앞의 4개 서버에 stale / io / offline / GPU 누락을 순서대로 적용합니다.'
	}
};
const statusText: Record<ServerState['status'], string> = {
	online: 'online',
	offline: 'offline',
	degraded: 'degraded',
	unknown: 'unknown'
};

let loading = $state(true);
let error = $state('');
let checkedAt = $state('');
let health = $state('unknown');
let serverRecords = $state<ServerRecord[]>([]);
let serverStates = $state<ServerState[]>([]);
let logsTotal = $state<number | null>(null);

const simulatedStates = $derived.by(() => applyDevScenario(serverStates, $activeDevScenario, Date.now()));
const activeScenarioMeta = $derived(scenarioMeta[$activeDevScenario]);
const onlineCount = $derived(simulatedStates.filter((state) => state.status === 'online').length);
const offlineCount = $derived(simulatedStates.filter((state) => state.status === 'offline').length);
const gpuCount = $derived(simulatedStates.reduce((total, state) => total + state.gpus.length, 0));
const rawStatus = $derived(JSON.stringify(simulatedStates, null, 2));

function sortServerRecords(records: ServerRecord[]): ServerRecord[] {
	return [...records].sort((a, b) => a.display_order - b.display_order || a.id - b.id);
}

function mergeDebugStates(records: ServerRecord[], statusMap: StatusMap): ServerState[] {
	const orderedRecords = sortServerRecords(records);
	const knownIds = new Set(orderedRecords.map((record) => record.id));
	const merged = orderedRecords.map((record) => {
		const normalized = normalizeServerState(statusMap[String(record.id)], record.id);
		return mergeServerRecordState(record, normalized);
	});

	for (const [idKey, rawState] of Object.entries(statusMap)) {
		const id = Number(idKey);
		if (!Number.isFinite(id) || knownIds.has(id)) continue;
		const normalized = normalizeServerState(rawState, id);
		if (!normalized) continue;
		merged.push(normalized);
	}

	return merged;
}

function statusClass(status: ServerState['status']): string {
	if (status === 'online') return 'text-emerald-300';
	if (status === 'offline') return 'text-red-200';
	if (status === 'degraded') return 'text-amber-200';
	return 'text-slate-300';
}

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
			json<ServerRecord[]>('/servers'),
			json<StatusMap>('/servers/status'),
			json<{ total?: number }>('/logs?limit=1')
		]);
		health = healthResult.status ?? 'unknown';
		serverRecords = sortServerRecords(serverResult);
		serverStates = mergeDebugStates(serverResult, stateResult);
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
					운영 서비스와 분리된 개발 DB 스냅샷을 조회합니다. 개발 백엔드 수집기는 활성화되어 있고,
					Slack Socket Mode는 비활성화되어 있습니다.
				</p>
			</div>
			<div class="flex gap-2">
				<a class="btn-ghost rounded-lg px-3 py-2 text-xs" href="/">대시보드</a>
				<button class="btn-ghost rounded-lg px-3 py-2 text-xs" onclick={() => void refresh()} disabled={loading}>
					{loading ? '확인 중…' : '새로고침'}
				</button>
			</div>
		</header>

		{#if devMode}
			<section data-dev-scenario-panel class="rounded-2xl border border-amber-400/30 bg-amber-500/10 p-5">
				<div class="flex flex-wrap items-start justify-between gap-4">
					<div class="space-y-2">
						<p class="text-xs font-semibold uppercase tracking-[0.24em] text-amber-200">SIMULATION</p>
						<h2 class="text-lg font-semibold text-white">Client-side dashboard scenario simulator</h2>
						<p class="max-w-3xl text-sm text-amber-50/90">
							State simulator is entirely client-side. It only transforms already-fetched ServerState snapshots in this
							browser session and never writes API/backend data.
						</p>
					</div>
					<div class="rounded-xl border border-amber-300/30 bg-black/10 px-4 py-3 text-sm">
						<p class="text-xs uppercase tracking-[0.2em] text-amber-100/80">active</p>
						<p class="mt-1 font-semibold text-white">{activeScenarioMeta.title}</p>
						<p class="mt-1 max-w-xs text-xs text-amber-50/80">{activeScenarioMeta.summary}</p>
					</div>
				</div>

				<div class="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
					{#each DEV_SCENARIOS as scenario}
						<button
							type="button"
							class={`rounded-xl border px-4 py-3 text-left transition ${$activeDevScenario === scenario ? 'border-amber-300 bg-amber-200/12 text-white' : 'border-surface-border bg-surface-card text-slate-200'}`}
							aria-pressed={$activeDevScenario === scenario}
							onclick={() => setDevScenario(scenario)}
						>
							<p class="text-sm font-semibold uppercase tracking-[0.14em]">{scenarioMeta[scenario].title}</p>
							<p class="mt-2 text-xs text-slate-300">{scenarioMeta[scenario].summary}</p>
						</button>
					{/each}
				</div>

				<div class="mt-4 flex flex-wrap items-center gap-3 text-sm">
					<button
						type="button"
						class="rounded-lg border border-amber-300/40 px-3 py-2 text-amber-100 disabled:cursor-not-allowed disabled:opacity-50"
						onclick={resetDevScenario}
						disabled={$activeDevScenario === 'normal'}
					>
						reset
					</button>
					<a class="rounded-lg border border-surface-border px-3 py-2 text-slate-100" href="/">dashboard link</a>
				</div>
			</section>
		{/if}

		{#if error}
			<div class="rounded-xl border border-red-400/40 bg-red-500/10 p-4 text-sm text-red-100">{error}</div>
		{/if}

		<section class="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
			<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">API health</p><p class="mt-1 text-xl font-semibold">{health}</p></div>
			<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">등록 서버</p><p class="mt-1 text-xl font-semibold">{serverRecords.length || simulatedStates.length}</p></div>
			<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">온라인</p><p class="mt-1 text-xl font-semibold">{onlineCount}</p></div>
			<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">오프라인</p><p class="mt-1 text-xl font-semibold">{offlineCount}</p></div>
			<div class="rounded-xl border border-surface-border bg-surface-card p-4"><p class="text-xs text-slate-400">스냅샷 GPU</p><p class="mt-1 text-xl font-semibold">{gpuCount}</p></div>
		</section>

		<section class="overflow-hidden rounded-xl border border-surface-border bg-surface-card">
			<div class="border-b border-surface-border px-4 py-3">
				<h2 class="font-medium">서버 스냅샷</h2>
				<p class="mt-1 text-xs text-slate-400">조회 시각: {checkedAt || '—'} · active scenario: {activeScenarioMeta.title}</p>
			</div>
			<div class="overflow-x-auto">
				<table class="w-full min-w-[880px] text-left text-sm">
					<thead class="bg-white/[0.03] text-xs text-slate-400">
						<tr>
							<th class="px-4 py-3">서버</th>
							<th class="px-4 py-3">상태</th>
							<th class="px-4 py-3">마지막 스냅샷</th>
							<th class="px-4 py-3">GPU</th>
							<th class="px-4 py-3">사유</th>
						</tr>
					</thead>
					<tbody>
						{#each simulatedStates as state (state.server_id)}
							<tr class="border-t border-surface-border align-top">
								<td class="px-4 py-3">
									<div class="font-medium">{state.server_name}</div>
									<div class="text-xs text-slate-500">{state.port ? `${state.host}:${state.port}` : state.host}</div>
								</td>
								<td class="px-4 py-3">
									<span class={statusClass(state.status)}>{statusText[state.status]}</span>
								</td>
								<td class="px-4 py-3 text-xs text-slate-300">{state.last_seen ?? '—'}</td>
								<td class="px-4 py-3">{state.gpus.length}</td>
								<td class="px-4 py-3 text-xs text-slate-300">{state.status_reason?.message ?? '—'}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>

		<details class="rounded-xl border border-surface-border bg-surface-card p-4">
			<summary class="cursor-pointer text-sm font-medium">시뮬레이션 적용 상태 JSON</summary>
			<pre class="mt-4 max-h-[32rem] overflow-auto rounded-lg bg-black/30 p-4 text-xs text-slate-300">{rawStatus}</pre>
		</details>
	</main>
</div>
