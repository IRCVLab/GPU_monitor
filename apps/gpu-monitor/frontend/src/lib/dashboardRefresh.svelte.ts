import { get } from 'svelte/store';
import { getServers, getServerStatus } from '$lib/api';
import { isServerStateEqual, normalizeServerState, serverStates } from '$lib/stores/servers';
import type { ServerRecord, ServerState } from '$lib/types';
import { mergeServerRecordState } from '$lib/utils/serverStateMerge';

const POLL_REFRESH_MS = 10_000;
const POLL_REQUEST_LEAD_MS = 1_000;
const VISIBLE_TICK_MS = 1_000;
const HIDDEN_TICK_MS = 30_000;
export const REFRESH_WARNING_AFTER_MS = POLL_REFRESH_MS * 2;

export function createDashboardRefresh() {
	const state = $state({
		nowMs: Date.now(),
		loaded: false,
		loadError: '',
		lastRefreshAtMs: 0,
		refreshFailureCount: 0,
		retryingInitialLoad: false
	});
	let ticker: ReturnType<typeof setInterval> | null = null;
	let autoRefreshTimer: ReturnType<typeof setTimeout> | null = null;
	let loadingGuard: ReturnType<typeof setTimeout> | null = null;
	let serverCatalog = new Map<number, ServerRecord>();
	let refreshInFlight = false;
	let queuedFullRefresh = false;

	function mergeStatusSnapshot(
		current: Map<number, ServerState>,
		statusMap: Record<number, ServerState>
	): boolean {
		const next = new Map(current);
		let changed = false;

		for (const [idStr, state] of Object.entries(statusMap)) {
			const id = Number(idStr);
			const normalized = normalizeServerState(state, id);
			if (!normalized) continue;

			const existing = next.get(id);
			const candidate = existing
				? {
						...existing,
						...normalized,
						display_order: normalized.display_order ?? existing.display_order
					}
				: normalized;
			const resolved = existing && isServerStateEqual(existing, candidate) ? existing : candidate;

			next.set(id, resolved);
			if (resolved !== existing) {
				changed = true;
			}
		}

		if (changed) {
			serverStates.set(next);
		}

		return changed;
	}

	async function refreshDashboardFull(): Promise<void> {
		const current = get(serverStates);
		const [serversResult, statusResult] = await Promise.allSettled([getServers(), getServerStatus()]);
		const servers = serversResult.status === 'fulfilled' ? serversResult.value : [];
		const statusMap = statusResult.status === 'fulfilled' ? statusResult.value : {};
		const hasServerCatalog = serversResult.status === 'fulfilled';

		if (hasServerCatalog) {
			serverCatalog = new Map(servers.map((record) => [record.id, record]));
		}

		if (servers.length === 0 && Object.keys(statusMap).length === 0) {
			if (serversResult.status === 'rejected' && statusResult.status === 'rejected') {
				throw serversResult.reason;
			}
		}

		const next = new Map<number, ServerState>();
		let changed = false;

		for (const record of servers) {
			const normalized = normalizeServerState(statusMap[record.id], record.id);
			const existing = current.get(record.id);
			const candidate = mergeServerRecordState(record, normalized, existing);
			const resolved = existing && isServerStateEqual(existing, candidate) ? existing : candidate;
			next.set(record.id, resolved);
			if (resolved !== existing) {
				changed = true;
			}
		}

		if (!hasServerCatalog) {
			for (const [idStr, state] of Object.entries(statusMap)) {
				const id = Number(idStr);
				if (next.has(id)) continue;
				const normalized = normalizeServerState(state, id);
				if (!normalized) continue;
				const existing = current.get(id);
				const resolved = existing && isServerStateEqual(existing, normalized) ? existing : normalized;
				next.set(id, resolved);
				if (resolved !== existing) {
					changed = true;
				}
			}
		}

		if (current.size !== next.size) {
			changed = true;
		}

		if (changed) {
			serverStates.set(next);
		}
	}

	async function refreshDashboardStatusOnly(): Promise<void> {
		const current = get(serverStates);
		if (current.size === 0) {
			await refreshDashboardFull();
			return;
		}

		const statusMap = await getServerStatus();
		mergeStatusSnapshot(current, statusMap);
	}

	function finishLoading() {
		state.loaded = true;
		if (loadingGuard !== null) {
			clearTimeout(loadingGuard);
			loadingGuard = null;
		}
	}

	async function reloadDashboard(fullRefresh = false): Promise<void> {
		if (refreshInFlight) {
			queuedFullRefresh ||= fullRefresh;
			return;
		}

		refreshInFlight = true;
		state.loadError = '';
		try {
			if (fullRefresh || get(serverStates).size === 0) {
				await refreshDashboardFull();
			} else {
				await refreshDashboardStatusOnly();
			}
			state.lastRefreshAtMs = Date.now();
			state.refreshFailureCount = 0;
		} catch (error) {
			state.loadError = error instanceof Error ? error.message : '대시보드 데이터를 불러오지 못했습니다.';
			state.refreshFailureCount += 1;
		} finally {
			const runQueuedFullRefresh = queuedFullRefresh;
			queuedFullRefresh = false;
			refreshInFlight = false;
			finishLoading();
			if (runQueuedFullRefresh) void reloadDashboard(true);
		}
	}

	function stop() {
		document.removeEventListener('visibilitychange', startTicker);
		if (ticker !== null) {
			clearInterval(ticker);
			ticker = null;
		}
		if (loadingGuard !== null) {
			clearTimeout(loadingGuard);
			loadingGuard = null;
		}
		if (autoRefreshTimer !== null) {
			clearTimeout(autoRefreshTimer);
			autoRefreshTimer = null;
		}
		queuedFullRefresh = false;
	}

	function currentTickIntervalMs(): number {
		return document.visibilityState === 'hidden' ? HIDDEN_TICK_MS : VISIBLE_TICK_MS;
	}

	function startTicker() {
		if (ticker !== null) {
			clearInterval(ticker);
		}

		state.nowMs = Date.now();
		ticker = setInterval(() => {
			state.nowMs = Date.now();
		}, currentTickIntervalMs());
	}

	function schedulePollingTick(delayMs: number): void {
		if (autoRefreshTimer !== null) {
			clearTimeout(autoRefreshTimer);
		}

		autoRefreshTimer = setTimeout(() => {
			schedulePollingTick(POLL_REFRESH_MS);
			void runAutoRefresh();
		}, delayMs);
	}

	function startPollingCadence(): void {
		schedulePollingTick(POLL_REFRESH_MS - POLL_REQUEST_LEAD_MS);
	}

	async function runAutoRefresh(): Promise<void> {
		if (refreshInFlight) return;

		await reloadDashboard();
	}

	async function retryInitialLoad() {
		if (state.retryingInitialLoad) return;
		state.retryingInitialLoad = true;
		try {
			await reloadDashboard(true);
		} finally {
			state.retryingInitialLoad = false;
		}
	}

	function start() {
		startTicker();
		loadingGuard = setTimeout(() => {
			state.loadError = '대시보드 응답이 지연되고 있습니다. 잠시 후 다시 시도하세요.';
			finishLoading();
		}, 6500);
		document.addEventListener('visibilitychange', startTicker);
		startPollingCadence();
		void reloadDashboard(true);
	}

	return {
		state,
		start,
		stop,
		reloadDashboard,
		retryInitialLoad,
		getServerRecord: (id: number) => serverCatalog.get(id)
	};
}
