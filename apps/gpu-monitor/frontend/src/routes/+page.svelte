<script lang="ts">
	import { browser } from '$app/environment';
	import { writable, derived, get } from 'svelte/store';
	import '$lib/styles/monitor-dashboard.css';
	import {
		serverStates,
		internalServers,
		externalServers,
		isServerStateEqual,
		normalizeServerState
	} from '$lib/stores/servers';
	import {
		colorTheme,
		colorThemeOptions,
		setColorTheme,
		setThemeMode,
		themeMode
	} from '$lib/stores/theme';
	import { dashboardView, setDashboardView } from '$lib/stores/dashboardPrefs';
	import { dashboardViewLabel } from '$lib/utils/dashboardViewLabel';
	import { placeOrderedMasonryItems } from '$lib/utils/orderedMasonry';
	import {
		compensateHeaderScrollPosition,
		HEADER_INDICATOR_TOP_MAX_PX,
		HEADER_INDICATOR_TOP_MIN_PX,
		shouldRevealSettledHeaderAtTop,
		type HeaderScrollDirection,
		updateHeaderVisibility
	} from '$lib/utils/headerVisibility';
	import { readCookie, writeCookie } from '$lib/utils/cookies';
	import { getServerStatus, getServers } from '$lib/api';
	import type { ServerRecord, ServerState } from '$lib/types';
	import { wsConnected } from '$lib/ws';
	import ServerCard from '$lib/components/ServerCard.svelte';
	import CompactDashboard from '$lib/components/CompactDashboard.svelte';
	import ServerForm from '$lib/components/ServerForm.svelte';
	import ServerDeleteModal from '$lib/components/ServerDeleteModal.svelte';

	type Tab = 'internal' | 'all' | 'external';
	const TAB_COOKIE = 'activeTab';
	const tabOrder: readonly Tab[] = ['internal', 'external', 'all'];

	function masonry(node: HTMLDivElement) {
		let frame = 0;
		let itemObserver: ResizeObserver | null = null;
		const containerObserver = new ResizeObserver(() => schedule());
		const mutationObserver = new MutationObserver(() => observeItems());

		function schedule(): void {
			if (frame !== 0) return;
			frame = requestAnimationFrame(layout);
		}

		function rowSize(styles: CSSStyleDeclaration): number {
			const value = Number.parseFloat(styles.getPropertyValue('--monitor-dashboard-masonry-row'));
			return Number.isFinite(value) && value > 0 ? value : 8;
		}

		function rowGap(styles: CSSStyleDeclaration): number {
			const value = Number.parseFloat(styles.rowGap);
			return Number.isFinite(value) && value >= 0 ? value : 16;
		}

		function layout(): void {
			frame = 0;
			const items = Array.from(node.children).filter(
				(child): child is HTMLElement => child instanceof HTMLElement
			);

			for (const child of items) {
				child.style.removeProperty('grid-column-start');
				child.style.removeProperty('grid-row-start');
				child.style.gridRowEnd = 'span 1';
			}

			const styles = getComputedStyle(node);
			const template = styles.gridTemplateColumns.trim();
			const currentColumnCount = template === '' || template === 'none' ? 1 : template.split(/\s+/).length;
			const currentRowSize = rowSize(styles);
			const currentGap = rowGap(styles);
			const spans = items.map((child) => {
				const height = child.getBoundingClientRect().height;
				return Math.max(1, Math.ceil((height + currentGap) / (currentRowSize + currentGap)));
			});
			const placements = placeOrderedMasonryItems({ columnCount: currentColumnCount, spans });

			items.forEach((child, index) => {
				const placement = placements[index];
				child.style.gridColumnStart = String(placement.gridColumnStart);
				child.style.gridRowStart = String(placement.gridRowStart);
				child.style.gridRowEnd = placement.gridRowEnd;
			});
		}

		function observeItems(): void {
			itemObserver?.disconnect();
			itemObserver = new ResizeObserver(() => schedule());
			for (const child of Array.from(node.children)) {
				if (!(child instanceof HTMLElement)) continue;
				itemObserver.observe(child);
			}
			schedule();
		}

		containerObserver.observe(node);
		mutationObserver.observe(node, { childList: true });
		observeItems();

		return {
			destroy() {
				if (frame !== 0) cancelAnimationFrame(frame);
				itemObserver?.disconnect();
				containerObserver.disconnect();
				mutationObserver.disconnect();
				for (const child of Array.from(node.children)) {
					if (child instanceof HTMLElement) {
						child.style.removeProperty('grid-column-start');
						child.style.removeProperty('grid-row-start');
						child.style.removeProperty('grid-row-end');
					}
				}
			}
		};
	}

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
	let headerIndicatorVisible = $state(false);
	let indicatorPanelOpen = $state(false);
	let indicatorElement = $state<HTMLDivElement | null>(null);
	let headerShellElement = $state<HTMLDivElement | null>(null);
	let headerSurfaceElement = $state<HTMLElement | null>(null);
	let headerScrollFrame: number | null = null;
	let headerPreviousY = 0;
	let headerScrollDirection: HeaderScrollDirection = null;
	let headerScrollDistance = 0;
	let headerTouchY: number | null = null;
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

	function currentHeaderScrollPosition(): number {
		const scrollY = Math.max(0, window.scrollY);
		if (!headerShellElement || !headerSurfaceElement) return scrollY;
		const renderedHeight = headerShellElement.getBoundingClientRect().height;

		if (shouldRevealSettledHeaderAtTop(scrollY, headerCompact, renderedHeight)) return 0;

		return compensateHeaderScrollPosition(
			scrollY,
			headerSurfaceElement.scrollHeight,
			renderedHeight
		);
	}

	function shouldRevealHeaderForUpwardIntent(): boolean {
		if (!headerShellElement) return false;
		return shouldRevealSettledHeaderAtTop(
			Math.max(0, window.scrollY),
			headerCompact,
			headerShellElement.getBoundingClientRect().height
		);
	}

	function handleHeaderWheel(event: WheelEvent): void {
		if (event.deltaY < 0 && shouldRevealHeaderForUpwardIntent()) revealHeader();
	}

	function handleHeaderTouchStart(event: TouchEvent): void {
		headerTouchY = event.touches[0]?.clientY ?? null;
	}

	function handleHeaderTouchMove(event: TouchEvent): void {
		const nextY = event.touches[0]?.clientY;
		if (nextY === undefined) return;
		const upwardIntent = headerTouchY !== null && nextY > headerTouchY;
		headerTouchY = nextY;
		if (upwardIntent && shouldRevealHeaderForUpwardIntent()) revealHeader();
	}

	function handleHeaderTouchEnd(): void {
		headerTouchY = null;
	}

	function handleHeaderTransitionEnd(event: TransitionEvent): void {
		if (event.target !== headerShellElement || event.propertyName !== 'grid-template-rows') return;
		headerPreviousY = currentHeaderScrollPosition();
		headerScrollDirection = null;
		headerScrollDistance = 0;
	}

	function revealHeader(): void {
		headerCompact = false;
		headerIndicatorVisible = false;
		indicatorPanelOpen = false;
		headerScrollDirection = null;
		headerScrollDistance = 0;
		if (browser) {
			headerPreviousY = currentHeaderScrollPosition();
		}
	}

	function updateHeaderFromScroll(): void {
		headerScrollFrame = null;
		const currentY = currentHeaderScrollPosition();
		const result = updateHeaderVisibility({
			currentY,
			previousY: headerPreviousY,
			direction: headerScrollDirection,
			accumulatedDelta: headerScrollDistance,
			currentCompact: headerCompact,
			reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
			viewportWidth: window.innerWidth
		});

		headerCompact = result.compact;
		headerIndicatorVisible = result.indicatorVisible;
		if (!result.indicatorVisible) indicatorPanelOpen = false;
		headerPreviousY = result.nextPreviousY;
		headerScrollDirection = result.nextDirection;
		headerScrollDistance = result.nextAccumulatedDelta;
	}

	function handleHeaderScroll(): void {
		if (headerScrollFrame !== null) return;
		headerScrollFrame = requestAnimationFrame(updateHeaderFromScroll);
	}

	function handleHeaderResize(): void {
		const currentY = currentHeaderScrollPosition();
		const result = updateHeaderVisibility({
			currentY,
			previousY: currentY,
			direction: headerScrollDirection,
			accumulatedDelta: 0,
			currentCompact: headerCompact,
			reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
			viewportWidth: window.innerWidth
		});

		headerCompact = result.compact;
		headerIndicatorVisible = result.indicatorVisible;
		if (!result.indicatorVisible) indicatorPanelOpen = false;
		headerPreviousY = result.nextPreviousY;
		headerScrollDirection = result.nextDirection;
		headerScrollDistance = result.nextAccumulatedDelta;
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
		headerPreviousY = currentHeaderScrollPosition();
		window.addEventListener('scroll', handleHeaderScroll, { passive: true });
		window.addEventListener('resize', handleHeaderResize, { passive: true });
		window.addEventListener('wheel', handleHeaderWheel, { passive: true });
		window.addEventListener('touchstart', handleHeaderTouchStart, { passive: true });
		window.addEventListener('touchmove', handleHeaderTouchMove, { passive: true });
		window.addEventListener('touchend', handleHeaderTouchEnd, { passive: true });

		void reloadDashboard(true).finally(() => {
			scheduleNextRefresh();
		});

		runtime.__monitoringV2PageCleanup = () => {
			unsubscribeTab();
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			window.removeEventListener('scroll', handleHeaderScroll);
			window.removeEventListener('resize', handleHeaderResize);
			window.removeEventListener('wheel', handleHeaderWheel);
			window.removeEventListener('touchstart', handleHeaderTouchStart);
			window.removeEventListener('touchmove', handleHeaderTouchMove);
			window.removeEventListener('touchend', handleHeaderTouchEnd);
			headerTouchY = null;
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

	const currentServers = derived(
		[activeTab, allServers, internalServers, externalServers],
		([$tab, $all, $int, $ext]) => {
			return $tab === 'all' ? $all : $tab === 'external' ? $ext : $int;
		}
	);

	let adminOpen = $state(false);
	let deleteOpen = $state(false);
	let editingServer = $state<ServerRecord | null>(null);
	let viewMenuOpen = $state(false);
	let viewMenuEl = $state<HTMLDivElement | null>(null);
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

	function toggleActionsMenu() {
		actionsMenuOpen = !actionsMenuOpen;
		if (actionsMenuOpen) revealHeader();
	}

	function openIndicatorPanel() {
		indicatorPanelOpen = true;
	}

	function closeIndicatorPanel() {
		indicatorPanelOpen = false;
	}

	function handleIndicatorFocusOut(event: FocusEvent) {
		const nextTarget = event.relatedTarget;
		if (nextTarget instanceof Node && indicatorElement && indicatorElement.contains(nextTarget)) return;
		indicatorPanelOpen = false;
	}

	function selectNetwork(tab: Tab) {
		activeTab.set(tab);
		indicatorPanelOpen = false;
	}


	function handleWindowClick(event: MouseEvent) {
		const target = event.target;
		if (!(target instanceof Node)) return;
		if (indicatorPanelOpen && indicatorElement && !indicatorElement.contains(target)) indicatorPanelOpen = false;
		if (viewMenuOpen && viewMenuEl && !viewMenuEl.contains(target)) viewMenuOpen = false;
		if (actionsMenuOpen && actionsMenuEl && !actionsMenuEl.contains(target)) actionsMenuOpen = false;
	}

	function handleWindowKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			indicatorPanelOpen = false;
			viewMenuOpen = false;
			actionsMenuOpen = false;
		}
		if (
			(event.key === 'Home' || event.key === 'ArrowUp' || event.key === 'PageUp') &&
			shouldRevealHeaderForUpwardIntent()
		) {
			revealHeader();
		}
	}

	const pageShellClass = 'max-w-7xl mx-auto';
	const pageMainClass = 'max-w-7xl mx-auto px-4 py-4 sm:px-6';
	const serverGridStyle = '--monitor-dashboard-card-min: 22rem;';
	const indicatorPanelId = 'ops-indicator-panel';
	const headerIndicatorStyle = `--ops-indicator-top-min: ${HEADER_INDICATOR_TOP_MIN_PX}px; --ops-indicator-top-max: ${HEADER_INDICATOR_TOP_MAX_PX}px;`;
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="dashboard-page min-h-screen bg-surface">
	<div bind:this={headerShellElement} ontransitionend={handleHeaderTransitionEnd} class="ops-header-shell" class:ops-header-compact={headerCompact} class:ops-header-indicator-visible={headerIndicatorVisible} class:ops-header-menu-open={viewMenuOpen || actionsMenuOpen}>
		<div class={`ops-indicator-anchor ${pageShellClass}`} aria-hidden={!headerIndicatorVisible} style={headerIndicatorStyle}>
			<div bind:this={indicatorElement} class="ops-indicator" role="group" aria-label="상태 표시기" onmouseenter={openIndicatorPanel} onmouseleave={closeIndicatorPanel} onfocusin={openIndicatorPanel} onfocusout={handleIndicatorFocusOut}>
				<button
					type="button"
					class="ops-indicator-trigger"
					aria-label={`${refreshHealthText()} · ${relativeTime(lastRefreshAtMs)}. 상세 상태 보기`}
					aria-expanded={indicatorPanelOpen}
					aria-controls={indicatorPanelId}
					onclick={openIndicatorPanel}
				>
					<span class:attention={!$wsConnected || refreshFailed} class="ops-indicator-dot" aria-hidden="true"></span>
				</button>
				<div id={indicatorPanelId} class="ops-indicator-panel" class:ops-indicator-panel-open={indicatorPanelOpen}>
					<span class="ops-indicator-status">{refreshHealthText()} · {relativeTime(lastRefreshAtMs)}</span>
					<span class="ops-indicator-divider" aria-hidden="true"></span>
					<div class="ops-indicator-network" role="group" aria-label="네트워크 필터">
						{#each $tabOptions as tab}
							<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} onclick={() => selectNetwork(tab.value)}><span>{tab.label}</span><span>{tab.count}</span></button>
						{/each}
					</div>
				</div>
			</div>
		</div>
		<header bind:this={headerSurfaceElement} class="ops-header border-b border-surface-border px-4 sm:px-6" inert={headerCompact} aria-hidden={headerCompact}>
			<div class={`ops-header-inner ${pageShellClass}`}>
				<div class="ops-identity">
					<h1>GPU Monitor</h1>
					<p class="ops-status" aria-live="polite">
						<span class:ops-status-attention={!$wsConnected || refreshFailed} class="ops-status-dot"></span>
						<span>{refreshHealthText()} · {relativeTime(lastRefreshAtMs)}</span>
						<span class="ops-status-separator" aria-hidden="true">•</span>
						<span>{nextRefreshText()}</span>
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
								<div class="ops-menu-row" role="group" aria-label="대시보드 보기">
									<span>보기</span>
									<button class:active={$dashboardView === 'default'} onclick={() => { setDashboardView('default'); viewMenuOpen = false; }}>{dashboardViewLabel('default')}</button>
									<button class:active={$dashboardView === 'compact'} onclick={() => { setDashboardView('compact'); viewMenuOpen = false; }}>{dashboardViewLabel('compact')}</button>
								</div>
								<div class="ops-view-divider"></div>
								<span class="ops-menu-label">색상 테마</span>
								<div class="ops-color-options" role="group" aria-label="색상 테마">
									{#each colorThemeOptions as option}
										<button
											class:active={$colorTheme === option.value}
											type="button"
											aria-label={option.label}
											aria-pressed={$colorTheme === option.value}
											style={`--swatch: ${option.color}`}
											onclick={() => {
												setColorTheme(option.value);
												viewMenuOpen = false;
											}}
										>
											<span></span><em>{option.label}</em>
										</button>
									{/each}
								</div>
							</div>
						{/if}
					</div>
					<div class="relative ops-admin-control" bind:this={actionsMenuEl}>
						<button class:active={actionsMenuOpen} class="ops-utility-action" onclick={toggleActionsMenu} aria-haspopup="true" aria-expanded={actionsMenuOpen}>관리</button>
						{#if actionsMenuOpen}<div class="ops-overflow-menu"><button class="ops-menu-link" onclick={() => { actionsMenuOpen = false; adminOpen = true; revealHeader(); }}>서버 등록</button><a class="ops-menu-link" href="/logs">이벤트 로그</a><a class="ops-menu-link" href="/debug">개발 진단</a><button class="ops-menu-danger" onclick={() => { actionsMenuOpen = false; deleteOpen = true; revealHeader(); }}>서버 삭제</button></div>{/if}
					</div>
					<button class="ops-mode-action" onclick={() => setThemeMode($themeMode === 'dark' ? 'light' : 'dark')} aria-label={$themeMode === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'}>
						{#if $themeMode === 'dark'}<span aria-hidden="true">☀</span>{:else}<span aria-hidden="true">☾</span>{/if}
					</button>
				</div>

				<nav class="ops-network ops-network-mobile" aria-label="네트워크 필터">
					{#each $tabOptions as tab}
						<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} onclick={() => selectNetwork(tab.value)}><span>{tab.label}</span><span>{tab.count}</span></button>
					{/each}
				</nav>
			</div>
		</header>
	</div>

	<main class={pageMainClass}>
		{#if !loaded}
			<section class="monitor-dashboard-state" role="status" aria-live="polite">
				<div class="monitor-dashboard-spinner" aria-hidden="true"></div>
				<p>불러오는 중...</p>
			</section>
		{:else if retryingInitialLoad && $serverStates.size === 0}
			<section class="monitor-dashboard-state" role="status" aria-live="polite">
				<div class="monitor-dashboard-spinner" aria-hidden="true"></div>
				<p>다시 불러오는 중...</p>
			</section>
		{:else if loadError && $serverStates.size === 0}
			<section class="monitor-dashboard-state" aria-live="polite">
				<p>{loadError}</p>
				<button class="monitor-dashboard-button" disabled={retryingInitialLoad} onclick={() => void retryInitialLoad()}>
					{retryingInitialLoad ? '다시 불러오는 중...' : '다시 시도'}
				</button>
			</section>
		{:else if $serverStates.size === 0}
			<section class="monitor-dashboard-state" aria-live="polite">
				<p>등록된 서버가 없습니다</p>
				<button class="monitor-dashboard-button" onclick={() => (adminOpen = true)}>
					+ 서버 등록하기
				</button>
			</section>
		{:else if $currentServers.length === 0}
			<section class="monitor-dashboard-state" aria-live="polite">
				<p>{$activeTab === 'all' ? '표시할 서버가 없습니다' : '이 네트워크에 서버 없음'}</p>
			</section>
		{:else if $dashboardView === 'compact'}
			<CompactDashboard servers={$currentServers} />
		{:else}
			<div class="monitor-dashboard-grid" style={serverGridStyle} use:masonry role="list">
				{#each $currentServers as server (server.server_id)}
					<div role="listitem" class="monitor-dashboard-card-item">
						<ServerCard {server} onEdit={handleEditServer} showNetwork={$activeTab === 'all'} />
					</div>
				{/each}
			</div>
		{/if}
	</main>
</div>

<ServerForm
	bind:open={adminOpen}
	bind:editServer={editingServer}
	onClose={() => {
		adminOpen = false;
		editingServer = null;
	}}
	onSaved={handleSaved}
/>

<ServerDeleteModal
	open={deleteOpen}
	onClose={() => (deleteOpen = false)}
	onDeleted={handleSaved}
/>
