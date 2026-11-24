<script lang="ts">
	import { browser, dev } from '$app/environment';
	import { tick, untrack } from 'svelte';
	import { cubicOut } from 'svelte/easing';
	import { writable, derived, get } from 'svelte/store';
	import { fly } from 'svelte/transition';
	import '$lib/styles/monitor-dashboard.css';
	import {
		serverStates,
		internalServers,
		externalServers
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
		setDashboardView
	} from '$lib/stores/dashboardPrefs';
	import { activeDevScenario, resetDevScenario } from '$lib/stores/devScenario';
	import { dashboardViewLabel } from '$lib/utils/dashboardViewLabel';
	import { applyDevScenario, type DevScenario } from '$lib/utils/devScenario';
	import { resolveDashboardShortcut } from '$lib/utils/dashboardShortcuts';
	import { masonry } from '$lib/actions/dashboardMasonry';
	import {
		compensateHeaderScrollPosition,
		HEADER_INDICATOR_TOP_MAX_PX,
		HEADER_INDICATOR_TOP_MIN_PX,
		shouldRevealSettledHeaderAtTop,
		type HeaderScrollDirection,
		updateHeaderVisibility
	} from '$lib/utils/headerVisibility';
	import { readCookie, writeCookie } from '$lib/utils/cookies';
	import { createDashboardRefresh, REFRESH_WARNING_AFTER_MS } from '$lib/dashboardRefresh.svelte';
	import type { ServerRecord, ServerState } from '$lib/types';
	import { wsConnected } from '$lib/ws';
	import ServerCard from '$lib/components/ServerCard.svelte';
	import CompactDashboard from '$lib/components/CompactDashboard.svelte';
	import ServerForm from '$lib/components/ServerForm.svelte';
	import ServerDeleteModal from '$lib/components/ServerDeleteModal.svelte';
	import RefreshRing from '$lib/components/RefreshRing.svelte';
	import DashboardHeader from '$lib/components/DashboardHeader.svelte';

	type Tab = 'internal' | 'all' | 'external';
	const TAB_COOKIE = 'activeTab';
	const tabOrder: readonly Tab[] = ['internal', 'external', 'all'];
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

	function readTab(): Tab {
		const value = readCookie(TAB_COOKIE);
		return tabOrder.includes(value as Tab) ? (value as Tab) : 'internal';
	}

	const activeTab = writable<Tab>(readTab());

	const REFRESH_WARNING_FAILURE_COUNT = 2;
	const refresh = createDashboardRefresh();
	const nowMs = $derived(refresh.state.nowMs);
	const loaded = $derived(refresh.state.loaded);
	const loadError = $derived(refresh.state.loadError);
	const lastRefreshAtMs = $derived(refresh.state.lastRefreshAtMs);
	const retryingInitialLoad = $derived(refresh.state.retryingInitialLoad);
	const { retryInitialLoad } = refresh;
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

		const unsubscribeTab = activeTab.subscribe((v) => {
			writeCookie(TAB_COOKIE, v);
		});
		headerPreviousY = currentHeaderScrollPosition();
		let themeButtonCacheFrame: number | null = requestAnimationFrame(cacheVisibleThemeButtonCenter);
		window.addEventListener('scroll', handleHeaderScroll, { passive: true });
		window.addEventListener('resize', handleHeaderResize, { passive: true });
		window.addEventListener('wheel', handleHeaderWheel, { passive: true });
		window.addEventListener('touchstart', handleHeaderTouchStart, { passive: true });
		window.addEventListener('touchmove', handleHeaderTouchMove, { passive: true });
		window.addEventListener('touchend', handleHeaderTouchEnd, { passive: true });
		refresh.start();

		let cleaned = false;
		const cleanup = () => {
			if (cleaned) return;
			cleaned = true;
			unsubscribeTab();
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
			refresh.stop();
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
		if (refresh.state.refreshFailureCount >= REFRESH_WARNING_FAILURE_COUNT) return '갱신 지연';
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
		([$servers, $scenario]) => dev ? applyDevScenario($servers, $scenario, Date.now()) : $servers
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
		await refresh.reloadDashboard(true);
	}

	function handleEditServer(server: ServerState) {
		const record = refresh.getServerRecord(server.server_id);
		if (!record) {
			refresh.state.loadError = '서버 설정 정보를 아직 불러오지 못했습니다. 잠시 후 다시 시도하세요.';
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
		<DashboardHeader bind:surface={headerSurfaceElement} compact={headerCompact} shellClass={pageShellClass}>
			{#snippet identity()}
					<h1>GPU <span class="ops-identity__long">Monitor</span></h1>
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
			{/snippet}
			{#snippet networkControls()}
					{#each $tabOptions as tab}
						<button class:active={$activeTab === tab.value} aria-pressed={$activeTab === tab.value} aria-keyshortcuts={tab.shortcut} data-shortcut-tooltip={tab.tooltip} onclick={() => selectNetwork(tab.value)}>
							<span>{tab.label}</span><span>{tab.count}</span>
						</button>
					{/each}
			{/snippet}
			{#snippet actions()}
					<a class="ops-utility-action ops-suite-link" href="/system">System</a>
					<a class="ops-utility-action ops-suite-link" href="http://166.104.167.11:505/">Storage</a>
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
						{#if actionsMenuOpen}<div class="ops-overflow-menu"><button class="ops-menu-link" onclick={() => { actionsMenuOpen = false; adminOpen = true; revealHeader(); }}>서버 등록</button><a class="ops-menu-link" href="/logs">이벤트 로그</a>{#if dev}<a class="ops-menu-link" href="/debug">개발 진단</a>{/if}<button class="ops-menu-danger" onclick={() => { actionsMenuOpen = false; deleteOpen = true; revealHeader(); }}>서버 삭제</button></div>{/if}
					</div>
					<button bind:this={themeModeButtonElement} class="ops-mode-action" onclick={() => void runThemeModeReveal(themeModeButtonElement)} aria-label={$themeMode === 'dark' ? '라이트 모드로 전환' : '다크 모드로 전환'} aria-busy={themeRevealLocked} aria-keyshortcuts="C" data-shortcut-tooltip="C 명암">
						<span class="ops-mode-icon-stack" aria-hidden="true">
							<svg class:active={$themeMode === 'dark'} class="ops-mode-icon ops-mode-icon--sun" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"></circle><path d="M12 2v2"></path><path d="M12 20v2"></path><path d="m4.93 4.93 1.41 1.41"></path><path d="m17.66 17.66 1.41 1.41"></path><path d="M2 12h2"></path><path d="M20 12h2"></path><path d="m6.34 17.66-1.41 1.41"></path><path d="m19.07 4.93-1.41 1.41"></path></svg>
							<svg class:active={$themeMode === 'light'} class="ops-mode-icon ops-mode-icon--moon" aria-hidden="true" viewBox="0 0 24 24" fill="currentColor"><path d="M20.2 14.3A7.9 7.9 0 0 1 9.7 3.8a.8.8 0 0 0-.8-1.1 9.6 9.6 0 1 0 12.4 12.4.8.8 0 0 0-1.1-.8Z"></path></svg>
						</span>
					</button>
			{/snippet}
		</DashboardHeader>
	</div>

	<main class={pageMainClass}>
		{#if dev && $activeDevScenario !== 'normal'}
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
