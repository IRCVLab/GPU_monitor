<script lang="ts">
	import { browser } from '$app/environment';
	import { writable, derived, get } from 'svelte/store';
	import {
		serverStates,
		internalServers,
		externalServers,
		isServerStateEqual,
		normalizeServerState
	} from '$lib/stores/servers';
	import { colorTheme, colorThemeOptions, setColorTheme, setThemeMode, themeMode } from '$lib/stores/theme';
	import { serverOrder, saveOrder } from '$lib/stores/order';
	import {
		dashboardTextScale,
		dashboardLayoutWidth,
		setDashboardTextScale,
		setDashboardLayoutWidth
	} from '$lib/stores/dashboardPrefs';
	import { readCookie, writeCookie } from '$lib/utils/cookies';
	import { getServerStatus, getServers } from '$lib/api';
	import type { ServerRecord, ServerState } from '$lib/types';
	import { wsConnected } from '$lib/ws';
	import ServerCard from '$lib/components/ServerCard.svelte';
	import ServerForm from '$lib/components/ServerForm.svelte';
	import ServerDeleteModal from '$lib/components/ServerDeleteModal.svelte';

	type Tab = 'internal' | 'all' | 'external';
	const HEADER_SCROLL_DELTA = 10;
	const TAB_COOKIE = 'activeTab';
	const tabOrder: readonly Tab[] = ['internal', 'external', 'all'];

	// ── Tab state (persisted in cookie) ────────────────────────────
	function readTab(): Tab {
		const value = readCookie(TAB_COOKIE);
		return tabOrder.includes(value as Tab) ? (value as Tab) : 'internal';
	}

	const activeTab = writable<Tab>(readTab());

	let nowMs = $state(Date.now());
	let ticker: ReturnType<typeof setInterval> | null = null;
	let autoRefreshTimer: ReturnType<typeof setTimeout> | null = null;
	let loaded = $state(false);
	let loadError = $state('');
	let loadingGuard: ReturnType<typeof setTimeout> | null = null;
	let serverCatalog = new Map<number, ServerRecord>();
	let lastRefreshAtMs = $state(0);
	let nextRefreshAtMs = $state<number | null>(null);
	let refreshInFlight = $state(false);
	let refreshFailed = $state(false);
	let headerCompact = $state(false);
	let headerScrollFrame: number | null = null;
	let headerPreviousY = 0;
	let retryingInitialLoad = $state(false);
	const POLL_REFRESH_MS = 10_000;
	const HIDDEN_REFRESH_MS = 60_000;
	const VISIBLE_TICK_MS = 1_000;
	const HIDDEN_TICK_MS = 30_000;

	function makeFallbackState(record: ServerRecord, existing?: ServerState): ServerState {
		return {
			server_id: record.id,
			server_name: record.name,
			host: record.host,
			port: record.port,
			network: record.network,
			status: existing?.status ?? 'unknown',
			status_reason: existing?.status_reason ?? null,
			last_seen: existing?.last_seen ?? null,
			gpus: existing?.gpus ?? [],
			system: existing?.system ?? null,
			storage: existing?.storage ?? null,
			display_order: record.display_order
		};
	}

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
			const candidate = normalized ?? makeFallbackState(record, existing);
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
		loaded = true;
		if (loadingGuard !== null) {
			clearTimeout(loadingGuard);
			loadingGuard = null;
		}
	}

	async function reloadDashboard(fullRefresh = false): Promise<void> {
		refreshInFlight = true;
		loadError = '';
		try {
			if (fullRefresh || get(serverStates).size === 0) {
				await refreshDashboardFull();
			} else {
				await refreshDashboardStatusOnly();
			}
			lastRefreshAtMs = Date.now();
			refreshFailed = false;
		} catch (error) {
			loadError = error instanceof Error ? error.message : '대시보드 데이터를 불러오지 못했습니다.';
			refreshFailed = true;
		} finally {
			refreshInFlight = false;
			finishLoading();
		}
	}

	function cleanupPageRuntime() {
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
		nextRefreshAtMs = null;
	}

	function currentTickIntervalMs(): number {
		if (!browser) return VISIBLE_TICK_MS;
		return document.visibilityState === 'hidden' ? HIDDEN_TICK_MS : VISIBLE_TICK_MS;
	}

	function currentRefreshIntervalMs(): number {
		if (!browser) return POLL_REFRESH_MS;
		if (document.visibilityState === 'hidden') return HIDDEN_REFRESH_MS;
		return POLL_REFRESH_MS;
	}

	function nextAlignedRefreshAtMs(intervalMs = currentRefreshIntervalMs(), now = Date.now()): number {
		return Math.floor(now / intervalMs) * intervalMs + intervalMs;
	}

	function startTicker() {
		if (ticker !== null) {
			clearInterval(ticker);
		}

		nowMs = Date.now();
		ticker = setInterval(() => {
			nowMs = Date.now();
		}, currentTickIntervalMs());
	}

	function scheduleNextRefresh(targetAtMs = nextAlignedRefreshAtMs()) {
		if (autoRefreshTimer !== null) {
			clearTimeout(autoRefreshTimer);
		}

		nextRefreshAtMs = targetAtMs;
		const delayMs = Math.max(0, targetAtMs - Date.now());
		autoRefreshTimer = setTimeout(() => {
			void runAutoRefresh();
		}, delayMs);
	}

	async function runAutoRefresh(): Promise<void> {
		if (refreshInFlight) {
			scheduleNextRefresh();
			return;
		}

		await reloadDashboard();
		scheduleNextRefresh();
	}

	function revealHeader(): void {
		headerCompact = false;
	}

	function updateHeaderFromScroll(): void {
		headerScrollFrame = null;
		const currentY = Math.max(0, window.scrollY);
		const delta = currentY - headerPreviousY;
		headerPreviousY = currentY;

		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || currentY <= 12) {
			headerCompact = false;
			return;
		}
		if (delta <= -HEADER_SCROLL_DELTA) {
			headerCompact = false;
		} else if (delta >= HEADER_SCROLL_DELTA && currentY > 56) {
			headerCompact = true;
		}
	}

	function handleHeaderScroll(): void {
		if (headerScrollFrame !== null) return;
		headerScrollFrame = requestAnimationFrame(updateHeaderFromScroll);
	}

	function initPageRuntime() {
		if (!browser) return;

		const runtime = globalThis as typeof globalThis & {
			__monitoringV2PageCleanup?: () => void;
		};
		runtime.__monitoringV2PageCleanup?.();

		startTicker();
		loadingGuard = setTimeout(() => {
			loadError = '대시보드 응답이 지연되고 있습니다. 잠시 후 다시 시도하세요.';
			finishLoading();
		}, 6500);

		const unsubscribeTab = activeTab.subscribe((v) => {
			writeCookie(TAB_COOKIE, v);
		});
		const handleVisibilityChange = () => {
			startTicker();
			if (!refreshInFlight) {
				scheduleNextRefresh();
			}
		};
		document.addEventListener('visibilitychange', handleVisibilityChange);
		headerPreviousY = window.scrollY;
		window.addEventListener('scroll', handleHeaderScroll, { passive: true });

		void reloadDashboard(true).finally(() => {
			scheduleNextRefresh();
		});

		runtime.__monitoringV2PageCleanup = () => {
			unsubscribeTab();
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			window.removeEventListener('scroll', handleHeaderScroll);
			if (headerScrollFrame !== null) {
				cancelAnimationFrame(headerScrollFrame);
				headerScrollFrame = null;
			}
			cleanupPageRuntime();
		};
	}

	initPageRuntime();

	function relativeTime(ms: number): string {
		if (ms === 0) return '–';
		const diff = Math.max(0, Math.floor((nowMs - ms) / 1000));
		if (diff < 60) return `${diff}초 전`;
		if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
		return `${Math.floor(diff / 3600)}시간 전`;
	}

	function nextRefreshText(): string {
		if (refreshInFlight) return '갱신 중';
		if (refreshFailed) return '지연';
		if (nextRefreshAtMs === null) return '준비 중';

		const delta = Math.ceil((nextRefreshAtMs - nowMs) / 1000);
		if (delta > 0) return `${delta}초 뒤`;
		return '곧 갱신';
	}

	function refreshHealthText(): string {
		if (refreshFailed) return '지연';
		if (!$wsConnected) return '확인 중';
		if (refreshInFlight) return '동기화';
		return '정상';
	}

	function orderServers(servers: ServerState[], order: number[]): ServerState[] {
		return [...servers].sort((a, b) => {
			const ai = order.indexOf(a.server_id);
			const bi = order.indexOf(b.server_id);
			if (ai === -1 && bi === -1) {
				return (a.display_order ?? a.server_id) - (b.display_order ?? b.server_id);
			}
			if (ai === -1) return 1;
			if (bi === -1) return -1;
			return ai - bi;
		});
	}

	function mergeVisibleOrder(globalIds: number[], visibleIds: number[]): number[] {
		const visibleSet = new Set(visibleIds);
		const merged = [...globalIds];
		let nextVisibleIndex = 0;

		for (let index = 0; index < merged.length; index += 1) {
			if (!visibleSet.has(merged[index])) continue;
			merged[index] = visibleIds[nextVisibleIndex] ?? merged[index];
			nextVisibleIndex += 1;
		}

		return merged;
	}

	const allServers = derived(serverStates, ($map) =>
		[...$map.values()].sort(
			(a, b) => (a.display_order ?? a.server_id) - (b.display_order ?? b.server_id)
		)
	);

	const tabOptions = derived([allServers, internalServers, externalServers], ([$all, $int, $ext]) => [
		{ value: 'internal' as Tab, label: '내부망', count: $int.length },
		{ value: 'external' as Tab, label: '외부망', count: $ext.length },
		{ value: 'all' as Tab, label: '전체', count: $all.length }
	]);

	// ── Derived current tab servers ─────────────────────────────────
	const currentServers = derived(
		[activeTab, allServers, internalServers, externalServers, serverOrder],
		([$tab, $all, $int, $ext, $order]) => {
			const selected = $tab === 'all' ? $all : $tab === 'external' ? $ext : $int;
			return orderServers(selected, $order);
		}
	);

	const globalOrderedIds = derived([allServers, serverOrder], ([$servers, $order]) =>
		orderServers($servers, $order).map((server) => server.server_id)
	);

	// ── Drag-to-reorder state ───────────────────────────────────────
	let dragging = $state<number | null>(null);
	let dragTarget = $state<number | null>(null);

	function dragStart(id: number) {
		dragging = id;
	}

	function dragOver(id: number) {
		dragTarget = id;
	}

	function handleDragOver(event: DragEvent, id: number) {
		event.preventDefault();
		dragOver(id);
	}

	function drop() {
		if (dragging === null || dragTarget === null || dragging === dragTarget) return;
		const list = [...get(currentServers)];
		const fromIdx = list.findIndex((s) => s.server_id === dragging);
		const toIdx = list.findIndex((s) => s.server_id === dragTarget);
		if (fromIdx === -1 || toIdx === -1) return;
		const [moved] = list.splice(fromIdx, 1);
		list.splice(toIdx, 0, moved);
		const mergedIds = mergeVisibleOrder(
			get(globalOrderedIds),
			list.map((server) => server.server_id)
		);
		void saveOrder(mergedIds);
	}

	function dragEnd() {
		dragging = null;
		dragTarget = null;
	}

	// ── Admin panel ─────────────────────────────────────────────────
	let adminOpen    = $state(false);
	let deleteOpen   = $state(false);
	let editingServer = $state<ServerRecord | null>(null);
	let viewMenuOpen = $state(false);
	let viewMenuEl = $state<HTMLDivElement | null>(null);
	let colorMenuOpen = $state(false);
	let colorMenuEl = $state<HTMLDivElement | null>(null);
	let actionsMenuOpen = $state(false);
	let actionsMenuEl = $state<HTMLDivElement | null>(null);

	async function handleSaved() {
		await reloadDashboard(true);
	}

	async function retryInitialLoad() {
		if (retryingInitialLoad) return;
		retryingInitialLoad = true;
		try {
			await reloadDashboard(true);
		} finally {
			retryingInitialLoad = false;
		}
	}

	function handleEditServer(server: ServerState) {
		const record = serverCatalog.get(server.server_id);
		if (!record) {
			loadError = '서버 설정 정보를 아직 불러오지 못했습니다. 잠시 후 다시 시도하세요.';
			return;
		}
		editingServer = { ...record };
		adminOpen = true;
	}

	function toggleViewMenu() {
		viewMenuOpen = !viewMenuOpen;
		if (viewMenuOpen) revealHeader();
	}

	function toggleColorMenu() {
		colorMenuOpen = !colorMenuOpen;
		if (colorMenuOpen) revealHeader();
	}

	function toggleActionsMenu() {
		actionsMenuOpen = !actionsMenuOpen;
		if (actionsMenuOpen) revealHeader();
	}

	function selectNetwork(tab: Tab) {
		activeTab.set(tab);
	}

	function handleWindowClick(event: MouseEvent) {
		const target = event.target;
		if (!(target instanceof Node)) return;
		if (viewMenuOpen && viewMenuEl && !viewMenuEl.contains(target)) viewMenuOpen = false;
		if (colorMenuOpen && colorMenuEl && !colorMenuEl.contains(target)) colorMenuOpen = false;
		if (actionsMenuOpen && actionsMenuEl && !actionsMenuEl.contains(target)) actionsMenuOpen = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			viewMenuOpen = false;
			colorMenuOpen = false;
			actionsMenuOpen = false;
		}
	}

	const pageShellClass = $derived(
		$dashboardLayoutWidth === 'full' ? 'w-full' : 'max-w-7xl mx-auto'
	);
	const pageMainClass = $derived(
		$dashboardLayoutWidth === 'full' ? 'w-full px-6 py-4' : 'max-w-7xl mx-auto px-6 py-4'
	);
	const serverGridMinWidth = $derived.by(() => {
		if ($dashboardTextScale === 'large') {
			return $dashboardLayoutWidth === 'full' ? '29rem' : '30rem';
		}
		if ($dashboardTextScale === 'default') {
			return $dashboardLayoutWidth === 'full' ? '23rem' : '23.25rem';
		}
		return $dashboardLayoutWidth === 'full' ? '21.5rem' : '22rem';
	});
	const serverGridClass = $derived(
		'grid gap-4'
	);
	const serverGridStyle = $derived(
		`grid-template-columns: repeat(auto-fit, minmax(min(100%, ${serverGridMinWidth}), 1fr));`
	);
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div
	class="dashboard-page min-h-screen bg-surface text-white"
	class:dashboard-layout-full={$dashboardLayoutWidth === 'full'}
>
	<div
		class="dashboard-scale-viewport"
		class:scale-small={$dashboardTextScale === 'small'}
		class:scale-default={$dashboardTextScale === 'default'}
		class:scale-large={$dashboardTextScale === 'large'}
		class:layout-full={$dashboardLayoutWidth === 'full'}
		class:layout-framed={$dashboardLayoutWidth === 'framed'}
	>
	<!-- Header -->
	<div class="ops-header-shell" class:ops-header-compact={headerCompact}>
		<header class="ops-header border-b border-surface-border px-4 sm:px-6">
			<div class={`ops-header-inner ${pageShellClass}`}>
				<div class="ops-identity">
					<h1>GPU Monitor</h1>
					<p class="ops-status" aria-live="polite">
						<span class:ops-status-attention={!$wsConnected || refreshFailed} class="ops-status-dot"></span>
						<span>{refreshHealthText()} · {relativeTime(lastRefreshAtMs)}</span>
					</p>
				</div>

				<nav class="ops-network ops-network-desktop" aria-label="네트워크 필터">
					{#each $tabOptions as tab}
						<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} onclick={() => selectNetwork(tab.value)}>
							<span>{tab.label}</span><span>{tab.count}</span>
						</button>
					{/each}
				</nav>

				<div class="ops-actions">
					<div class="relative ops-direct-control" bind:this={viewMenuEl}>
						<button class:active={viewMenuOpen} class="ops-utility-action" onclick={toggleViewMenu} aria-haspopup="true" aria-expanded={viewMenuOpen}>보기 <span aria-hidden="true">⌄</span></button>
						{#if viewMenuOpen}
							<div class="ops-popover ops-view-menu">
								<div class="ops-menu-row" role="group" aria-label="레이아웃 폭"><span>폭</span><button class:active={$dashboardLayoutWidth === 'framed'} onclick={() => { setDashboardLayoutWidth('framed'); viewMenuOpen = false; }}>기본</button><button class:active={$dashboardLayoutWidth === 'full'} onclick={() => { setDashboardLayoutWidth('full'); viewMenuOpen = false; }}>전체</button></div>
								<div class="ops-menu-row" role="group" aria-label="화면 배율"><span>배율</span>{#each ['small', 'default', 'large'] as scale}<button class:active={$dashboardTextScale === scale} onclick={() => { setDashboardTextScale(scale as 'small' | 'default' | 'large'); viewMenuOpen = false; }}>{scale === 'small' ? '작게' : scale === 'default' ? '기본' : '크게'}</button>{/each}</div>
							</div>
						{/if}
					</div>
					<div class="relative ops-direct-control" bind:this={colorMenuEl}>
						<button class:active={colorMenuOpen} class="ops-palette-action" onclick={toggleColorMenu} aria-label="색상 테마" aria-haspopup="true" aria-expanded={colorMenuOpen}><span class="ops-palette-mark"><i></i><i></i><i></i></span></button>
						{#if colorMenuOpen}
							<div class="ops-popover ops-color-menu" role="group" aria-label="색상 테마">
								<span class="ops-menu-label">색상 테마</span>
								<div class="ops-color-options">{#each colorThemeOptions as option}<button class:active={$colorTheme === option.value} type="button" aria-label={option.label} aria-pressed={$colorTheme === option.value} style={`--swatch: ${option.color}`} onclick={() => { setColorTheme(option.value); colorMenuOpen = false; }}><span></span><em>{option.label}</em></button>{/each}</div>
							</div>
						{/if}
					</div>
					<button class="ops-mode-action" onclick={() => setThemeMode($themeMode === 'dark' ? 'light' : 'dark')} aria-label={$themeMode === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'}>
						{#if $themeMode === 'dark'}<span aria-hidden="true">☀</span>{:else}<span aria-hidden="true">☾</span>{/if}
					</button>
					<button class="ops-primary-action" onclick={() => { adminOpen = true; revealHeader(); }}>서버 등록</button>
					<a href="/logs" class="ops-quiet-action">로그</a>
					<div class="relative ops-admin-control" bind:this={actionsMenuEl}>
						<button class:active={actionsMenuOpen} class="ops-utility-action" onclick={toggleActionsMenu} aria-haspopup="true" aria-expanded={actionsMenuOpen}>관리</button>
						{#if actionsMenuOpen}<div class="ops-overflow-menu"><a class="ops-menu-link" href="/debug">개발 진단</a><button class="ops-menu-danger" onclick={() => { actionsMenuOpen = false; deleteOpen = true; revealHeader(); }}>서버 삭제</button></div>{/if}
					</div>
				</div>

				<nav class="ops-network ops-network-mobile" aria-label="네트워크 필터">
					{#each $tabOptions as tab}<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} onclick={() => selectNetwork(tab.value)}><span>{tab.label}</span><span>{tab.count}</span></button>{/each}
				</nav>
			</div>
		</header>
	</div>

	<!-- Main content -->
	<main class={pageMainClass}>
		{#if !loaded}
			<!-- 초기 로딩 -->
			<div class="flex items-center justify-center h-64 text-white/30 text-sm">
				<div class="text-center">
					<div class="w-6 h-6 border border-white/20 border-t-white/60 rounded-full animate-spin mx-auto mb-3"></div>
					불러오는 중...
				</div>
			</div>
		{:else if retryingInitialLoad && $serverStates.size === 0}
			<div class="flex items-center justify-center h-64 text-white/30 text-sm">
				<div class="text-center">
					<div class="w-6 h-6 border border-white/20 border-t-white/60 rounded-full animate-spin mx-auto mb-3"></div>
					다시 불러오는 중...
				</div>
			</div>
		{:else if loadError && $serverStates.size === 0}
			<div class="flex flex-col items-center justify-center h-64 gap-3 text-white/40 text-sm">
				<span>{loadError}</span>
				<button
					class="btn-ghost text-xs border border-white/10 rounded-lg px-3 py-1.5"
					disabled={retryingInitialLoad}
					onclick={() => void retryInitialLoad()}
				>
					{retryingInitialLoad ? '다시 불러오는 중...' : '다시 시도'}
				</button>
			</div>
		{:else if $serverStates.size === 0}
			<!-- 서버 없음 -->
			<div class="flex flex-col items-center justify-center h-64 gap-3 text-white/30 text-sm">
				<span>등록된 서버가 없습니다</span>
				<button class="btn-ghost text-xs border border-white/10 rounded-lg px-3 py-1.5"
					onclick={() => (adminOpen = true)}>
					+ 서버 등록하기
				</button>
			</div>
		{:else if $currentServers.length === 0}
			<!-- 현재 탭에 서버 없음 -->
			<div class="flex items-center justify-center h-64 text-white/30 text-sm">
				{$activeTab === 'all' ? '표시할 서버가 없습니다' : '이 네트워크에 서버 없음'}
			</div>
		{:else}
			<!-- Server grid: drag-to-reorder, 1 col / 2 col tablet / 3 col desktop -->
			<div
				class={serverGridClass}
				style={serverGridStyle}
				role="list"
			>
				{#each $currentServers as server (server.server_id)}
					<div
						role="listitem"
						draggable="true"
						ondragstart={() => dragStart(server.server_id)}
						ondragover={(event) => handleDragOver(event, server.server_id)}
						ondrop={() => drop()}
						ondragend={() => dragEnd()}
						class="cursor-grab active:cursor-grabbing"
						class:opacity-40={dragging === server.server_id}
						class:ring-1={dragTarget === server.server_id && dragTarget !== dragging}
						class:ring-blue-500={dragTarget === server.server_id && dragTarget !== dragging}
					>
							<ServerCard {server} onEdit={handleEditServer} />
						</div>
					{/each}
				</div>
		{/if}
	</main>
	</div>
</div>

<ServerForm
	bind:open={adminOpen}
	bind:editServer={editingServer}
	onClose={() => { adminOpen = false; editingServer = null; }}
	onSaved={handleSaved}
/>

<ServerDeleteModal
	open={deleteOpen}
	onClose={() => (deleteOpen = false)}
	onDeleted={handleSaved}
/>
