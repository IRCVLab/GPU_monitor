<script lang="ts">
	import { browser } from '$app/environment';
	import { tick, untrack } from 'svelte';
	import { cubicOut } from 'svelte/easing';
	import { writable, derived, get } from 'svelte/store';
	import { fly } from 'svelte/transition';
	import '$lib/styles/monitor-dashboard.css';
	import {
		serverStates,
		internalServers,
		externalServers,
		isServerStateEqual,
		normalizeServerState
	} from '$lib/stores/servers';
	import {
		materialTheme,
		materialThemeOptions,
		setMaterialTheme,
		setThemeMode,
		themeMode,
		type ThemeMode
	} from '$lib/stores/theme';
	import { serverOrder, saveOrder } from '$lib/stores/order';
	import {
		dashboardLayout,
		dashboardLayoutWidth,
		dashboardView,
		setDashboardLayout,
		setDashboardLayoutWidth,
		setDashboardView,
		type DashboardLayoutWidth
	} from '$lib/stores/dashboardPrefs';
	import { activeDevScenario, resetDevScenario } from '$lib/stores/devScenario';
	import { dashboardViewLabel } from '$lib/utils/dashboardViewLabel';
	import { applyDevScenario, type DevScenario } from '$lib/utils/devScenario';
	import { resolveDashboardShortcut } from '$lib/utils/dashboardShortcuts';
	import { countResolvedGridTracks, placeOrderedMasonryItems } from '$lib/utils/orderedMasonry';
	import { mergeServerRecordState } from '$lib/utils/serverStateMerge';
	import {
		animateFlip,
		documentRect,
		type FlipRect
	} from '$lib/utils/layoutFlip';
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
	import RefreshRing from '$lib/components/RefreshRing.svelte';

	type Tab = 'internal' | 'all' | 'external';
	const TAB_COOKIE = 'activeTab';
	const tabOrder: readonly Tab[] = ['internal', 'external', 'all'];
	const devMode = import.meta.env.DEV;
	const devScenarioLabels: Record<Exclude<DevScenario, 'normal'>, string> = {
		stale: '갱신 지연',
		io: 'I/O 병목',
		offline: 'SSH 연결 실패',
		gpu_missing: 'GPU visibility mismatch · GPU 누락',
		mixed: '복합 장애'
	};
	const dashboardViewTransition = {
		y: browser && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 8,
		opacity: browser && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 1 : 0.72,
		duration: browser && window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 210,
		easing: cubicOut
	};

	type MasonryOptions = {
		enabled: boolean;
		layoutWidth: DashboardLayoutWidth;
	};

	function masonry(node: HTMLDivElement, initialOptions: MasonryOptions) {
		let frame = 0;
		let enabled = initialOptions.enabled;
		let layoutWidth = initialOptions.layoutWidth;
		let responsiveLayoutInvalidated = false;
		let itemObserver: ResizeObserver | null = null;
		let previousRects = new Map<HTMLElement, FlipRect>();
		let assignedColumns = new Map<HTMLElement, number>();
		let measuredHeights = new Map<HTMLElement, number>();
		let assignedItems: HTMLElement[] = [];
		let assignedColumnCount = 0;
		const layoutAnimations = new Map<HTMLElement, Animation>();
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

		function masonryItemBorderBoxBlockSize(child: HTMLElement, entry?: ResizeObserverEntry): number {
			const borderBoxSize = entry?.borderBoxSize as ResizeObserverSize | readonly ResizeObserverSize[] | undefined;
			const firstBorderBoxSize = Array.isArray(borderBoxSize) ? borderBoxSize[0] : borderBoxSize;
			const blockSize = firstBorderBoxSize?.blockSize;
			return Number.isFinite(blockSize) && blockSize > 0 ? blockSize : child.getBoundingClientRect().height;
		}

		function clearPlacement(child: HTMLElement): void {
			child.style.removeProperty('grid-column-start');
			child.style.removeProperty('grid-row-start');
			child.style.removeProperty('grid-row-end');
		}

		function clearAssignments(): void {
			assignedColumns.clear();
			assignedItems = [];
			assignedColumnCount = 0;
		}

		function clearMasonryState(): void {
			clearAssignments();
			measuredHeights.clear();
		}

		function layout(): void {
			frame = 0;
			const items = Array.from(node.children).filter(
				(child): child is HTMLElement => child instanceof HTMLElement
			);
			const visualRects = new Map(
				items.map((child) => {
					const rect = child.getBoundingClientRect();
					return [
						child,
						{
							left: rect.left + window.scrollX,
							top: rect.top + window.scrollY,
							width: rect.width,
							height: rect.height
						}
					];
				})
			);
			const animateResponsiveResize = responsiveLayoutInvalidated;

			if (responsiveLayoutInvalidated) {
				clearAssignments();
				measuredHeights.clear();
				for (const child of items) clearPlacement(child);
				responsiveLayoutInvalidated = false;
			}

			if (enabled) {
				const styles = getComputedStyle(node);
				const currentColumnCount = countResolvedGridTracks(styles.gridTemplateColumns);
				const columnCountChanged = currentColumnCount !== assignedColumnCount;
				const structureChanged =
					columnCountChanged ||
					items.length !== assignedItems.length ||
					items.some((child, index) => child !== assignedItems[index]);

				if (structureChanged) clearAssignments();
				if (columnCountChanged) measuredHeights.clear();

				const currentRowSize = rowSize(styles);
				const currentGap = rowGap(styles);
				const spans = items.map((child) => {
					const height = measuredHeights.get(child) ?? masonryItemBorderBoxBlockSize(child);
					measuredHeights.set(child, height);
					return Math.max(1, Math.ceil((height + currentGap) / (currentRowSize + currentGap)));
				});
				const placements = placeOrderedMasonryItems({
					columnCount: currentColumnCount,
					spans,
					preferredColumns: items.map((child) => assignedColumns.get(child) ?? null)
				});

				items.forEach((child, index) => {
					const placement = placements[index];
					assignedColumns.set(child, placement.gridColumnStart);
					child.style.gridColumnStart = String(placement.gridColumnStart);
					child.style.gridRowStart = String(placement.gridRowStart);
					child.style.gridRowEnd = placement.gridRowEnd;
				});
				assignedItems = items;
				assignedColumnCount = currentColumnCount;
			} else {
				clearMasonryState();
				for (const child of items) clearPlacement(child);
			}

			const nextRects = new Map(items.map((child) => [child, documentRect(child)]));
			const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
			const moves = items.flatMap((child) => {
				const previousFinal = previousRects.get(child);
				const next = nextRects.get(child);
				if (!previousFinal || !next) return [];
				if (
					Math.abs(previousFinal.left - next.left) < 0.5 &&
					Math.abs(previousFinal.top - next.top) < 0.5 &&
					(!animateResponsiveResize || Math.abs((previousFinal.width ?? 0) - (next.width ?? 0)) < 0.5)
				) return [];
				const previous = layoutAnimations.has(child) ? visualRects.get(child) ?? previousFinal : previousFinal;
				return [{ child, previous, next }];
			});

			if (moves.length > 0) {
				for (const animation of layoutAnimations.values()) animation.cancel();
				layoutAnimations.clear();
			}

			for (const { child, previous, next } of moves) {
				const animation = animateFlip(child, previous, next, reducedMotion, animateResponsiveResize);
				if (!animation) continue;
				layoutAnimations.set(child, animation);
				animation.addEventListener('finish', () => layoutAnimations.delete(child), { once: true });
				animation.addEventListener('cancel', () => layoutAnimations.delete(child), { once: true });
			}
			previousRects = nextRects;
		}

		function observeItems(): void {
			itemObserver?.disconnect();
			clearAssignments();
			const currentItems = Array.from(node.children).filter(
				(child): child is HTMLElement => child instanceof HTMLElement
			);
			const currentSet = new Set(currentItems);
			for (const child of Array.from(measuredHeights.keys())) {
				if (!currentSet.has(child)) measuredHeights.delete(child);
			}
			itemObserver = new ResizeObserver((entries) => {
				for (const entry of entries) {
					if (entry.target instanceof HTMLElement) {
						measuredHeights.set(entry.target, masonryItemBorderBoxBlockSize(entry.target, entry));
					}
				}
				schedule();
			});
			for (const child of currentItems) itemObserver.observe(child);
			schedule();
		}

		containerObserver.observe(node);
		mutationObserver.observe(node, { childList: true });
		observeItems();

		return {
			update(nextOptions: MasonryOptions) {
				const widthChanged = nextOptions.layoutWidth !== layoutWidth;
				if (enabled !== nextOptions.enabled) {
					clearMasonryState();
				}
				if (widthChanged) {
					responsiveLayoutInvalidated = true;
				}
				enabled = nextOptions.enabled;
				layoutWidth = nextOptions.layoutWidth;
				schedule();
			},
			destroy() {
				if (frame !== 0) cancelAnimationFrame(frame);
				for (const animation of layoutAnimations.values()) animation.cancel();
				layoutAnimations.clear();
				previousRects.clear();
				clearMasonryState();
				itemObserver?.disconnect();
				containerObserver.disconnect();
				mutationObserver.disconnect();
				for (const child of Array.from(node.children)) {
					if (child instanceof HTMLElement) clearPlacement(child);
				}
			}
		};
	}

	function readTab(): Tab {
		const value = readCookie(TAB_COOKIE);
		return tabOrder.includes(value as Tab) ? (value as Tab) : 'internal';
	}

	const activeTab = writable<Tab>(readTab());

	const POLL_REFRESH_MS = 10_000;
	const POLL_REQUEST_LEAD_MS = 1_000;
	const REFRESH_WARNING_FAILURE_COUNT = 2;
	const REFRESH_WARNING_AFTER_MS = POLL_REFRESH_MS * 2;
	const VISIBLE_TICK_MS = 1_000;
	const HIDDEN_TICK_MS = 30_000;

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
	let refreshFailureCount = $state(0);
	let headerCompact = $state(false);
	let headerIndicatorVisible = $state(false);
	let indicatorPanelOpen = $state(false);
	let indicatorElement = $state<HTMLDivElement | null>(null);
	let fixedRefreshRingElement = $state<HTMLSpanElement | null>(null);
	let headerRefreshRingElement = $state<HTMLSpanElement | null>(null);
	let headerIndicatorHandoffAnimation = $state<Animation | null>(null);
	let headerIndicatorHandoffActive = $state(false);
	let headerIndicatorHandoffFrame: number | null = null;
	let headerIndicatorHandoffOverlay: HTMLElement | null = null;
	let headerIndicatorHandoffSourceRect: DOMRect | null = null;
	let themeModeButtonElement = $state<HTMLButtonElement | null>(null);
	let themeRevealLocked = $state(false);
	let lastThemeModeButtonCenter = $state<ThemeRevealOrigin | null>(null);
	let headerShellElement = $state<HTMLDivElement | null>(null);
	let headerSurfaceElement = $state<HTMLElement | null>(null);
	let headerScrollFrame: number | null = null;
	let headerPreviousY = 0;
	let headerScrollDirection: HeaderScrollDirection = null;
	let headerScrollDistance = 0;
	let headerTouchY: number | null = null;
	let retryingInitialLoad = $state(false);

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
			refreshFailureCount = 0;
		} catch (error) {
			loadError = error instanceof Error ? error.message : '대시보드 데이터를 불러오지 못했습니다.';
			refreshFailureCount += 1;
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

	function startTicker() {
		if (ticker !== null) {
			clearInterval(ticker);
		}

		nowMs = Date.now();
		ticker = setInterval(() => {
			nowMs = Date.now();
		}, currentTickIntervalMs());
	}

	function schedulePollingTick(delayMs: number): void {
		if (autoRefreshTimer !== null) {
			clearTimeout(autoRefreshTimer);
		}

		nextRefreshAtMs = Date.now() + delayMs;
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

	function currentHeaderScrollPosition(): number {
		const scrollY = Math.max(0, window.scrollY);
		if (!headerShellElement || !headerSurfaceElement) return scrollY;
		const renderedHeight = headerShellElement.getBoundingClientRect().height;
		const renderedHeaderHeight = Math.max(0, renderedHeight);
		const expandedHeaderHeight = Math.max(0, headerSurfaceElement.scrollHeight);

		if (shouldRevealSettledHeaderAtTop(scrollY, headerCompact, renderedHeaderHeight)) return 0;

		return compensateHeaderScrollPosition(
			scrollY,
			expandedHeaderHeight,
			renderedHeaderHeight
		);
	}

	function shouldRevealHeaderForUpwardIntent(): boolean {
		if (!headerShellElement) return false;
		const renderedHeaderHeight = Math.max(0, headerShellElement.getBoundingClientRect().height);
		return shouldRevealSettledHeaderAtTop(
			Math.max(0, window.scrollY),
			headerCompact,
			renderedHeaderHeight
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

	type HeaderIndicatorHandoffDirection = 'collapse' | 'reveal';
	type ThemeRevealOrigin = { x: number; y: number };
	type NativeViewTransition = { finished: Promise<unknown> };
	type ViewTransitionCapableDocument = Document & {
		startViewTransition?: (updateCallback: () => void) => NativeViewTransition;
	};

	function cleanupHeaderIndicatorHandoff(): void {
		if (headerIndicatorHandoffFrame !== null) {
			cancelAnimationFrame(headerIndicatorHandoffFrame);
			headerIndicatorHandoffFrame = null;
		}
		headerIndicatorHandoffAnimation?.cancel();
		headerIndicatorHandoffAnimation = null;
		headerIndicatorHandoffActive = false;
		headerIndicatorHandoffOverlay?.remove();
		headerIndicatorHandoffOverlay = null;
		headerIndicatorHandoffSourceRect = null;
	}

	function scheduleHeaderIndicatorHandoff(direction: HeaderIndicatorHandoffDirection): void {
		if (!browser) return;
		const sourceElement = direction === 'collapse' ? headerRefreshRingElement : fixedRefreshRingElement;
		const targetElement = direction === 'collapse' ? fixedRefreshRingElement : headerRefreshRingElement;
		if (!sourceElement || !targetElement) return;
		cleanupHeaderIndicatorHandoff();
		headerIndicatorHandoffSourceRect = sourceElement.getBoundingClientRect();
		headerIndicatorHandoffFrame = requestAnimationFrame(() => {
			headerIndicatorHandoffFrame = null;
			void runHeaderIndicatorHandoff(direction);
		});
	}

	async function runHeaderIndicatorHandoff(direction: HeaderIndicatorHandoffDirection): Promise<void> {
		const sourceRect = headerIndicatorHandoffSourceRect;
		const targetElement = direction === 'collapse' ? fixedRefreshRingElement : headerRefreshRingElement;
		if (!browser || !sourceRect || !targetElement) return;
		await tick();
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
			cleanupHeaderIndicatorHandoff();
			return;
		}
		const targetRect = targetElement.getBoundingClientRect();
		if (sourceRect.width <= 0 || sourceRect.height <= 0 || targetRect.width <= 0 || targetRect.height <= 0) {
			cleanupHeaderIndicatorHandoff();
			return;
		}
		const overlay = (direction === 'collapse' ? headerRefreshRingElement : fixedRefreshRingElement)?.cloneNode(true) as HTMLElement | null;
		if (!overlay) return;
		headerIndicatorHandoffActive = true;
		overlay.classList.add('ops-refresh-handoff');
		overlay.setAttribute('aria-hidden', 'true');
		overlay.style.left = `${sourceRect.left}px`;
		overlay.style.top = `${sourceRect.top}px`;
		overlay.style.width = `${sourceRect.width}px`;
		overlay.style.height = `${sourceRect.height}px`;
		document.body.appendChild(overlay);
		headerIndicatorHandoffOverlay = overlay;

		const deltaX = targetRect.left - sourceRect.left;
		const deltaY = targetRect.top - sourceRect.top;
		const scaleX = targetRect.width / sourceRect.width;
		const scaleY = targetRect.height / sourceRect.height;
		headerIndicatorHandoffAnimation = overlay.animate([
			{ opacity: 0.96, transform: 'translate3d(0, 0, 0) scale(1, 1)' },
			{ opacity: 0.98, offset: 0.78, transform: `translate3d(${deltaX}px, ${deltaY}px, 0) scale(${scaleX}, ${scaleY})` },
			{ opacity: 0, transform: `translate3d(${deltaX}px, ${deltaY}px, 0) scale(${scaleX}, ${scaleY})` }
		], { duration: 220, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards' });
		headerIndicatorHandoffAnimation.addEventListener('finish', cleanupHeaderIndicatorHandoff, { once: true });
		headerIndicatorHandoffAnimation.addEventListener('cancel', () => {
			headerIndicatorHandoffOverlay?.remove();
			headerIndicatorHandoffOverlay = null;
		}, { once: true });
	}

	function revealHeader(): void {
		if (headerCompact || headerIndicatorVisible) scheduleHeaderIndicatorHandoff('reveal');
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

		if (!headerCompact && result.compact && result.indicatorVisible) scheduleHeaderIndicatorHandoff('collapse');
		if (headerCompact && !result.compact) scheduleHeaderIndicatorHandoff('reveal');
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
		cacheVisibleThemeButtonCenter();
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

	function initPageRuntime(): (() => void) | undefined {
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
		};
		document.addEventListener('visibilitychange', handleVisibilityChange);
		headerPreviousY = currentHeaderScrollPosition();
		let themeButtonCacheFrame: number | null = requestAnimationFrame(cacheVisibleThemeButtonCenter);
		window.addEventListener('scroll', handleHeaderScroll, { passive: true });
		window.addEventListener('resize', handleHeaderResize, { passive: true });
		window.addEventListener('wheel', handleHeaderWheel, { passive: true });
		window.addEventListener('touchstart', handleHeaderTouchStart, { passive: true });
		window.addEventListener('touchmove', handleHeaderTouchMove, { passive: true });
		window.addEventListener('touchend', handleHeaderTouchEnd, { passive: true });
		startPollingCadence();
		void reloadDashboard(true);

		let cleaned = false;
		const cleanup = () => {
			if (cleaned) return;
			cleaned = true;
			unsubscribeTab();
			document.removeEventListener('visibilitychange', handleVisibilityChange);
			window.removeEventListener('scroll', handleHeaderScroll);
			window.removeEventListener('resize', handleHeaderResize);
			window.removeEventListener('wheel', handleHeaderWheel);
			window.removeEventListener('touchstart', handleHeaderTouchStart);
			window.removeEventListener('touchmove', handleHeaderTouchMove);
			window.removeEventListener('touchend', handleHeaderTouchEnd);
			headerTouchY = null;
			if (themeButtonCacheFrame !== null) {
				cancelAnimationFrame(themeButtonCacheFrame);
				themeButtonCacheFrame = null;
			}
			if (headerScrollFrame !== null) {
				cancelAnimationFrame(headerScrollFrame);
				headerScrollFrame = null;
			}
			clearContinuityFocus();
			cleanupHeaderIndicatorHandoff();
			themeRevealLocked = false;
			document.documentElement.style.removeProperty('--theme-reveal-x');
			document.documentElement.style.removeProperty('--theme-reveal-y');
			document.documentElement.style.removeProperty('--theme-reveal-radius');
			cleanupPageRuntime();
			if (runtime.__monitoringV2PageCleanup === cleanup) {
				delete runtime.__monitoringV2PageCleanup;
			}
		};
		runtime.__monitoringV2PageCleanup = cleanup;
		return cleanup;
	}

	$effect(() => untrack(() => initPageRuntime()));


	function relativeTime(ms: number): string {
		if (ms === 0) return '–';
		const diff = Math.max(0, Math.floor((nowMs - ms) / 1000));
		if (diff < 60) return `${diff}초 전`;
		if (diff < 3600) return `${Math.floor(diff / 60)}분 전`;
		return `${Math.floor(diff / 3600)}시간 전`;
	}


	function refreshWarningText(): string {
		if (refreshFailureCount >= REFRESH_WARNING_FAILURE_COUNT) return '갱신 지연';
		if (
			!$wsConnected &&
			lastRefreshAtMs > 0 &&
			nowMs - lastRefreshAtMs >= REFRESH_WARNING_AFTER_MS
		) return '연결 지연';
		return '';
	}

	function refreshHealthText(): string {
		return refreshWarningText() || '정상';
	}

	function refreshIssueText(): string {
		const warning = refreshWarningText();
		return warning ? `${warning} · ${relativeTime(lastRefreshAtMs)}` : '';
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
		{ value: 'internal' as Tab, label: '내부망', count: $int.length, shortcut: '1', tooltip: '1 내부망' },
		{ value: 'external' as Tab, label: '외부망', count: $ext.length, shortcut: '2', tooltip: '2 외부망' },
		{ value: 'all' as Tab, label: '전체', count: $all.length, shortcut: '3', tooltip: '3 전체망' }
	]);

	const currentServers = derived(
		[activeTab, allServers, internalServers, externalServers, serverOrder],
		([$tab, $all, $int, $ext, $order]) => {
			const selected = $tab === 'all' ? $all : $tab === 'external' ? $ext : $int;
			return orderServers(selected, $order);
		}
	);

	const displayServers = derived(
		[currentServers, activeDevScenario],
		([$servers, $scenario]) => applyDevScenario($servers, $scenario, Date.now())
	);

	const globalOrderedIds = derived([allServers, serverOrder], ([$servers, $order]) =>
		orderServers($servers, $order).map((server) => server.server_id)
	);

	let dragging = $state<number | null>(null);
	let dragTarget = $state<number | null>(null);

	function dragStart(id: number) {
		dragging = id;
	}

	function handleDragOver(event: DragEvent, id: number) {
		event.preventDefault();
		dragTarget = id;
	}

	function drop() {
		if (dragging === null || dragTarget === null || dragging === dragTarget) return;
		const list = [...get(currentServers)];
		const fromIndex = list.findIndex((server) => server.server_id === dragging);
		const toIndex = list.findIndex((server) => server.server_id === dragTarget);
		if (fromIndex === -1 || toIndex === -1) return;
		const [moved] = list.splice(fromIndex, 1);
		list.splice(toIndex, 0, moved);
		void saveOrder(
			mergeVisibleOrder(
				get(globalOrderedIds),
				list.map((server) => server.server_id)
			)
		);
		dragging = null;
		dragTarget = null;
	}

	function dragEnd() {
		dragging = null;
		dragTarget = null;
	}

	let adminOpen = $state(false);
	let deleteOpen = $state(false);
	let editingServer = $state<ServerRecord | null>(null);
	let viewMenuOpen = $state(false);
	let viewMenuEl = $state<HTMLDivElement | null>(null);
	let actionsMenuOpen = $state(false);
	let actionsMenuEl = $state<HTMLDivElement | null>(null);
	let focusedServerId = $state<number | null>(null);
	let continuityFocusServerId = $state<number | null>(null);

	const serverCardElements = new Map<number, HTMLDivElement>();
	let continuityFocusTimer: ReturnType<typeof setTimeout> | null = null;

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

	function trackServerCard(node: HTMLDivElement, serverId: number) {
		serverCardElements.set(serverId, node);
		return {
			update(nextServerId: number) {
				if (nextServerId === serverId) return;
				serverCardElements.delete(serverId);
				serverId = nextServerId;
				serverCardElements.set(serverId, node);
			},
			destroy() {
				serverCardElements.delete(serverId);
			}
		};
	}

	function clearContinuityFocus(): void {
		if (continuityFocusTimer !== null) {
			clearTimeout(continuityFocusTimer);
			continuityFocusTimer = null;
		}
		continuityFocusServerId = null;
	}

	function nextFrame(): Promise<void> {
		return new Promise((resolve) => requestAnimationFrame(() => resolve()));
	}

	async function focusServerCard(serverId: number): Promise<void> {
		if (!browser) return;
		await tick();
		await nextFrame();

		const card = serverCardElements.get(serverId);
		if (!card) return;

		card.scrollIntoView({
			block: 'nearest',
			inline: 'nearest',
			behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
		});
		card.focus({ preventScroll: true });

		continuityFocusServerId = serverId;
		if (continuityFocusTimer !== null) clearTimeout(continuityFocusTimer);
		continuityFocusTimer = window.setTimeout(() => {
			continuityFocusTimer = null;
			if (continuityFocusServerId === serverId) continuityFocusServerId = null;
			if (focusedServerId === serverId) focusedServerId = null;
		}, 1400);
	}

	async function handleOpenFull(serverId: number): Promise<void> {
		revealHeader();
		viewMenuOpen = false;
		actionsMenuOpen = false;
		focusedServerId = serverId;
		setDashboardView('default');
		await focusServerCard(serverId);
	}

	function openIndicatorPanel() {
		if (indicatorPanelOpen) return;
		indicatorPanelOpen = true;
	}

	function closeIndicatorPanel() {
		if (!indicatorPanelOpen) return;
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


	function farthestCornerRadius(originX: number, originY: number): number {
		return Math.hypot(
			Math.max(originX, window.innerWidth - originX),
			Math.max(originY, window.innerHeight - originY)
		) + 24;
	}

	function fallbackThemeRevealCenter(): ThemeRevealOrigin {
		return lastThemeModeButtonCenter ?? {
			x: Math.max(24, window.innerWidth - 32),
			y: Math.max(24, HEADER_INDICATOR_TOP_MAX_PX + 16)
		};
	}

	function cacheVisibleThemeButtonCenter(): void {
		readVisibleThemeButtonCenter(themeModeButtonElement);
	}

	function isVisibleThemeRevealSource(originElement: HTMLElement | null): originElement is HTMLElement {
		if (!originElement) return false;
		if (originElement.closest('.ops-header-compact')) return false;
		const rect = originElement.getBoundingClientRect();
		if (rect.width <= 0 || rect.height <= 0 || rect.bottom <= 0 || rect.right <= 0 || rect.left >= window.innerWidth || rect.top >= window.innerHeight) return false;
		return true;
	}

	function readVisibleThemeButtonCenter(originElement: HTMLElement | null): ThemeRevealOrigin | null {
		if (!isVisibleThemeRevealSource(originElement)) return null;
		const rect = originElement.getBoundingClientRect();
		const center = { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
		lastThemeModeButtonCenter = center;
		return center;
	}

	async function runThemeModeReveal(
		originElement: HTMLElement | null = themeModeButtonElement,
		originOverride: ThemeRevealOrigin | null = null,
		shouldRestoreFocus = true
	): Promise<void> {
		if (!browser || themeRevealLocked) return;
		const document = globalThis.document as ViewTransitionCapableDocument;
		const nextMode: ThemeMode = $themeMode === 'dark' ? 'light' : 'dark';
		const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
		const visibleOrigin = readVisibleThemeButtonCenter(originElement);
		const origin = originOverride ?? visibleOrigin ?? fallbackThemeRevealCenter();
		const originX = origin.x;
		const originY = origin.y;
		const radius = farthestCornerRadius(originX, originY);
		const rootStyle = document.documentElement.style;
		themeRevealLocked = true;
		rootStyle.setProperty('--theme-reveal-x', `${originX}px`);
		rootStyle.setProperty('--theme-reveal-y', `${originY}px`);
		rootStyle.setProperty('--theme-reveal-radius', `${radius}px`);
		const supportsViewTransition = typeof document.startViewTransition === 'function';
		try {
			if (reducedMotion || !supportsViewTransition) {
				setThemeMode(nextMode);
				return;
			}

			const transition = document.startViewTransition(() => {
				setThemeMode(nextMode);
			});
			await transition.finished;
		} finally {
			if (shouldRestoreFocus && originElement) originElement.focus({ preventScroll: true });
			themeRevealLocked = false;
			rootStyle.removeProperty('--theme-reveal-x');
			rootStyle.removeProperty('--theme-reveal-y');
			rootStyle.removeProperty('--theme-reveal-radius');
		}
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

		if (adminOpen || deleteOpen) return;
		const shortcut = resolveDashboardShortcut(event);
		if (!shortcut) return;

		event.preventDefault();
		viewMenuOpen = false;
		actionsMenuOpen = false;
		indicatorPanelOpen = false;

		switch (shortcut.type) {
			case 'toggle-view':
				setDashboardView($dashboardView === 'compact' ? 'default' : 'compact');
				break;
			case 'select-network':
				selectNetwork(shortcut.tab);
				break;
			case 'toggle-theme': {
				void runThemeModeReveal(themeModeButtonElement, null, false);
				break;
			}
		}
	}

	const pageShellClass = $derived(
		$dashboardLayoutWidth === 'full' ? 'w-full' : 'max-w-7xl mx-auto'
	);
	const pageMainClass = $derived(
		$dashboardLayoutWidth === 'full' ? 'w-full px-4 py-4 sm:px-6' : 'max-w-7xl mx-auto px-4 py-4 sm:px-6'
	);
	const serverGridStyle = '--monitor-dashboard-card-min: 22rem;';
	const indicatorPanelId = 'ops-indicator-panel';
	const headerIndicatorStyle = `--ops-indicator-top-min: ${HEADER_INDICATOR_TOP_MIN_PX}px; --ops-indicator-top-max: ${HEADER_INDICATOR_TOP_MAX_PX}px;`;
</script>

<svelte:window onclick={handleWindowClick} onkeydown={handleWindowKeydown} />

<div class="dashboard-page min-h-screen bg-surface" class:dashboard-layout-full={$dashboardLayoutWidth === 'full'}>
	<div bind:this={headerShellElement} ontransitionend={handleHeaderTransitionEnd} class="ops-header-shell" class:ops-header-compact={headerCompact} class:ops-header-indicator-visible={headerIndicatorVisible} class:ops-header-menu-open={viewMenuOpen || actionsMenuOpen}>
		<div class={`ops-indicator-anchor ${pageShellClass}`} aria-hidden={!headerIndicatorVisible} style={headerIndicatorStyle}>
			<div bind:this={indicatorElement} class="ops-indicator" role="group" aria-label="상태 표시기" onmouseenter={openIndicatorPanel} onmouseleave={closeIndicatorPanel} onfocusin={openIndicatorPanel} onfocusout={handleIndicatorFocusOut}>
				<button
					type="button"
					class="ops-indicator-trigger"
					aria-label={refreshIssueText() ? `${refreshIssueText()}. 상세 상태 보기` : '정상. 상세 상태 보기'}
					aria-expanded={indicatorPanelOpen}
					aria-controls={indicatorPanelId}
					onclick={openIndicatorPanel}
				>
					<span bind:this={fixedRefreshRingElement} class="ops-refresh-ring-wrap" class:ops-refresh-ring-wrap--handoff-active={headerIndicatorHandoffActive}><RefreshRing attention={Boolean(refreshWarningText())} variant="floating" /></span>
				</button>
				<div id={indicatorPanelId} class="ops-indicator-panel" class:ops-indicator-panel-open={indicatorPanelOpen} aria-hidden={!indicatorPanelOpen} inert={!indicatorPanelOpen}>
					{#if refreshWarningText()}
						<span class="ops-indicator-status">{refreshIssueText()}</span>
						<span class="ops-indicator-divider" aria-hidden="true"></span>
					{/if}
					<div class="ops-indicator-network" role="group" aria-label="네트워크 필터">
						{#each $tabOptions as tab}
							<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} aria-keyshortcuts={tab.shortcut} data-shortcut-tooltip={tab.tooltip} onclick={() => selectNetwork(tab.value)}><span>{tab.label}</span><span>{tab.count}</span></button>
						{/each}
					</div>
				</div>
			</div>
		</div>
		<header bind:this={headerSurfaceElement} class="ops-header border-b border-surface-border" inert={headerCompact} aria-hidden={headerCompact}>
			<div class={`ops-header-inner ${pageShellClass} px-4 sm:px-6`}>
				<div class="ops-identity">
					<h1>GPU Monitor</h1>
					<p
						class="ops-status"
						aria-live="polite"
						aria-label={refreshIssueText() || '정상'}
						title={refreshIssueText() || undefined}
					>
						<span bind:this={headerRefreshRingElement} class="ops-refresh-ring-wrap" class:ops-refresh-ring-wrap--handoff-active={headerIndicatorHandoffActive}><RefreshRing attention={Boolean(refreshWarningText())} variant="header" /></span>
						{#if refreshWarningText()}
							<span class="ops-status-label">{refreshWarningText()}</span>
						{/if}
					</p>
				</div>

				<nav class="ops-network ops-network-desktop" aria-label="네트워크 필터">
					{#each $tabOptions as tab}
						<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} aria-keyshortcuts={tab.shortcut} data-shortcut-tooltip={tab.tooltip} onclick={() => selectNetwork(tab.value)}>
							<span>{tab.label}</span><span>{tab.count}</span>
						</button>
					{/each}
				</nav>

				<div class="ops-actions">
					<a class="ops-utility-action ops-suite-link" href="http://127.0.0.1:8088/">Storage</a>
					<div class="relative ops-direct-control" bind:this={viewMenuEl}>
						<button class:active={viewMenuOpen} class="ops-utility-action" onclick={toggleViewMenu} aria-haspopup="true" aria-expanded={viewMenuOpen} aria-keyshortcuts="V">{dashboardViewLabel($dashboardView)} <span aria-hidden="true">⌄</span></button>
						{#if viewMenuOpen}
							<div class="ops-popover ops-view-menu">
								<div class="ops-menu-row" role="group" aria-label="대시보드 보기">
									<span>모드</span>
									<button class:active={$dashboardView === 'default'} onclick={() => { setDashboardView('default'); viewMenuOpen = false; }}>{dashboardViewLabel('default')}</button>
									<button class:active={$dashboardView === 'compact'} onclick={() => { setDashboardView('compact'); viewMenuOpen = false; }}>{dashboardViewLabel('compact')}</button>
								</div>
								<div class="ops-view-divider"></div>
								<div class="ops-menu-row" role="group" aria-label="레이아웃 폭">
									<span>폭</span>
									<button class:active={$dashboardLayoutWidth === 'framed'} aria-pressed={$dashboardLayoutWidth === 'framed'} onclick={() => { setDashboardLayoutWidth('framed'); viewMenuOpen = false; }}>기본</button>
									<button class:active={$dashboardLayoutWidth === 'full'} aria-pressed={$dashboardLayoutWidth === 'full'} onclick={() => { setDashboardLayoutWidth('full'); viewMenuOpen = false; }}>전체</button>
								</div>
								{#if $dashboardView === 'default'}
									<div class="ops-view-divider"></div>
									<div class="ops-menu-row" role="group" aria-label="카드 배치">
										<span>배치</span>
										<button class:active={$dashboardLayout === 'grid'} aria-pressed={$dashboardLayout === 'grid'} onclick={() => { setDashboardLayout('grid'); viewMenuOpen = false; }}>그리드</button>
										<button class:active={$dashboardLayout === 'masonry'} aria-pressed={$dashboardLayout === 'masonry'} onclick={() => { setDashboardLayout('masonry'); viewMenuOpen = false; }}>빈틈 없이</button>
									</div>
									<div class="ops-view-divider"></div>
								{/if}
								<span class="ops-menu-label">Theme / Material</span>
								<div class="ops-material-options" role="group" aria-label="재질 프리셋">
									{#each materialThemeOptions as option}
										<button
											class:active={$materialTheme === option.value}
											type="button"
											aria-label={option.label}
											aria-pressed={$materialTheme === option.value}
											data-material-preview={option.value}
											onclick={() => {
												setMaterialTheme(option.value);
												viewMenuOpen = false;
											}}
										>
											<span class="ops-material-tile" aria-hidden="true"><i></i></span><em>{option.label}</em>
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
					<button bind:this={themeModeButtonElement} class="ops-mode-action" onclick={() => void runThemeModeReveal(themeModeButtonElement)} aria-label={$themeMode === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'} aria-busy={themeRevealLocked} aria-keyshortcuts="C" data-shortcut-tooltip="C 명암">
						{#if $themeMode === 'dark'}
							<svg class="ops-mode-icon ops-mode-icon--sun" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path></svg>
						{:else}
							<svg class="ops-mode-icon ops-mode-icon--moon" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M20.2 14.3A7.9 7.9 0 0 1 9.7 3.8a.8.8 0 0 0-.8-1.1 9.6 9.6 0 1 0 12.4 12.4.8.8 0 0 0-1.1-.8Z"></path></svg>
						{/if}
					</button>
				</div>

				<nav class="ops-network ops-network-mobile" aria-label="네트워크 필터">
					{#each $tabOptions as tab}
						<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} aria-keyshortcuts={tab.shortcut} data-shortcut-tooltip={tab.tooltip} onclick={() => selectNetwork(tab.value)}><span>{tab.label}</span><span>{tab.count}</span></button>
					{/each}
				</nav>
			</div>
		</header>
	</div>

	<main class={pageMainClass}>
		{#if devMode && $activeDevScenario !== 'normal'}
			<aside class="monitor-dev-simulation" role="status" aria-live="polite">
				<div class="monitor-dev-simulation__copy">
					<strong>SIMULATION · {devScenarioLabels[$activeDevScenario]}</strong>
					<span>실제 서버 데이터는 변경되지 않습니다.</span>
				</div>
				<div class="monitor-dev-simulation__actions">
					<a href="/debug">시나리오 설정</a>
					<button type="button" onclick={resetDevScenario}>실제 상태로 복귀</button>
				</div>
			</aside>
		{/if}

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
		{:else}
			{#key $dashboardView}
				<div class="ops-dashboard-view-stage" in:fly={dashboardViewTransition}>
					{#if $dashboardView === 'compact'}
						<CompactDashboard servers={$displayServers} onOpenFull={handleOpenFull} />
					{:else}
						<div
							class="monitor-dashboard-grid"
							class:monitor-dashboard-grid--masonry={$dashboardLayout === 'masonry'}
							style={serverGridStyle}
							use:masonry={{
								enabled: $dashboardLayout === 'masonry',
								layoutWidth: $dashboardLayoutWidth
							}}
							role="list"
						>
							{#each $displayServers as server (server.server_id)}
								<div
									role="listitem"
									tabindex="-1"
									draggable="true"
									ondragstart={() => dragStart(server.server_id)}
									ondragover={(event) => handleDragOver(event, server.server_id)}
									ondrop={drop}
									ondragend={dragEnd}
									use:trackServerCard={server.server_id}
									class="monitor-dashboard-card-item cursor-grab active:cursor-grabbing"
									class:monitor-dashboard-card-item--continuity-focus={continuityFocusServerId === server.server_id}
									class:opacity-40={dragging === server.server_id}
									class:ring-1={dragTarget === server.server_id && dragTarget !== dragging}
									class:ring-blue-500={dragTarget === server.server_id && dragTarget !== dragging}
								>
									<ServerCard {server} {nowMs} onEdit={handleEditServer} showNetwork={$activeTab === 'all'} />
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/key}
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

<style>
	.monitor-dev-simulation {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 0.75rem;
		padding: 0.55rem 0.7rem;
		border: 1px solid color-mix(in srgb, #f59e0b 32%, var(--ops-border));
		border-radius: 0.8rem;
		background: color-mix(in srgb, #f59e0b 8%, var(--ops-card));
		color: var(--ops-fg);
	}

	.monitor-dev-simulation__copy,
	.monitor-dev-simulation__actions {
		display: flex;
		align-items: center;
		gap: 0.6rem;
	}

	.monitor-dev-simulation__copy strong {
		font-size: 0.7rem;
		letter-spacing: 0.08em;
		color: color-mix(in srgb, #f59e0b 82%, var(--ops-fg));
	}

	.monitor-dev-simulation__copy span,
	.monitor-dev-simulation__actions {
		font-size: 0.68rem;
		color: color-mix(in srgb, var(--ops-fg) 66%, transparent);
	}

	.monitor-dev-simulation__actions a,
	.monitor-dev-simulation__actions button {
		min-height: 1.8rem;
		padding: 0.32rem 0.62rem;
		border: 1px solid color-mix(in srgb, var(--ops-border) 88%, transparent);
		border-radius: 999px;
		background: color-mix(in srgb, var(--ops-card) 92%, transparent);
		color: color-mix(in srgb, var(--ops-fg) 88%, transparent);
		font: inherit;
		cursor: pointer;
	}

	.monitor-dashboard-card-item--continuity-focus {
		border-radius: 1.25rem;
		box-shadow:
			0 0 0 2px color-mix(in srgb, var(--ops-primary) 44%, transparent),
			0 0 0 8px color-mix(in srgb, var(--ops-primary) 10%, transparent);
		transition: box-shadow 220ms ease;
	}

	@media (max-width: 720px) {
		.monitor-dev-simulation,
		.monitor-dev-simulation__copy {
			align-items: flex-start;
			flex-direction: column;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.monitor-dashboard-card-item--continuity-focus {
			transition: none;
		}
	}
</style>
