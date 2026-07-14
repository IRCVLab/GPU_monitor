<script lang="ts">
	import { browser } from '$app/environment';
	import { tick } from 'svelte';
	import '$lib/styles/monitor-compact.css';
	import type { ServerState } from '$lib/types';
	import CompactServerRow from '$lib/components/CompactServerRow.svelte';
	import CompactServerDetail from '$lib/components/CompactServerDetail.svelte';

	type CompactTooltip = {
		title: string;
		users: string[];
		hiddenUserCount: number;
		left: number;
		top: number;
	};

	let {
		servers,
		showNetwork = false
	}: {
		servers: ServerState[];
		showNetwork?: boolean;
	} = $props();

	const DESKTOP_MEDIA_QUERY = '(min-width: 1200px)';
	const TOOLTIP_WIDTH_ESTIMATE = 220;
	const TOOLTIP_VIEWPORT_MARGIN = 12;

	let selectedServerId = $state<number | null>(null);
	let mobileSheetOpen = $state(false);
	let lastTriggerRowId = $state<number | null>(null);
	let isDesktop = $state(false);
	let rowRefs = new Map<number, HTMLElement>();
	let activeTooltip = $state<CompactTooltip | null>(null);

	const selectedServer = $derived(
		selectedServerId === null
			? null
			: servers.find((server) => server.server_id === selectedServerId) ?? null
	);

	function registerRow(serverId: number, element: HTMLElement | null): void {
		if (element) {
			rowRefs.set(serverId, element);
			return;
		}
		rowRefs.delete(serverId);
	}

	function openServer(serverId: number): void {
		selectedServerId = serverId;
		lastTriggerRowId = serverId;
		if (!isDesktop) {
			mobileSheetOpen = true;
		}
	}

	async function restoreFocus(): Promise<void> {
		if (lastTriggerRowId === null) return;
		await tick();
		rowRefs.get(lastTriggerRowId)?.focus();
	}

	function closeSelection(): void {
		selectedServerId = null;
	}

	function closeDesktopDetail(): void {
		activeTooltip = null;
		closeSelection();
		void restoreFocus();
	}

	function closeMobileSheet(): void {
		activeTooltip = null;
		mobileSheetOpen = false;
		closeSelection();
		void restoreFocus();
	}

	function updateTooltip(next: CompactTooltip | null): void {
		if (!next) {
			activeTooltip = null;
			return;
		}

		const clampedLeft = Math.min(
			Math.max(next.left, TOOLTIP_VIEWPORT_MARGIN),
			window.innerWidth - TOOLTIP_WIDTH_ESTIMATE - TOOLTIP_VIEWPORT_MARGIN
		);

		activeTooltip = {
			...next,
			left: clampedLeft,
			top: Math.max(next.top, TOOLTIP_VIEWPORT_MARGIN)
		};
	}

	function handleWindowKeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape') return;
		if (mobileSheetOpen) {
			event.preventDefault();
			closeMobileSheet();
			return;
		}
		if (isDesktop && selectedServerId !== null) {
			event.preventDefault();
			closeDesktopDetail();
		}
	}

	function clearEphemeralUi(): void {
		activeTooltip = null;
	}

	$effect(() => {
		if (!browser) return;

		const media = window.matchMedia(DESKTOP_MEDIA_QUERY);
		const updateLayout = (): void => {
			isDesktop = media.matches;
			if (media.matches) {
				mobileSheetOpen = false;
			}
		};

		updateLayout();
		media.addEventListener('change', updateLayout);
		window.addEventListener('scroll', clearEphemeralUi, true);
		window.addEventListener('resize', clearEphemeralUi);
		return () => {
			media.removeEventListener('change', updateLayout);
			window.removeEventListener('scroll', clearEphemeralUi, true);
			window.removeEventListener('resize', clearEphemeralUi);
		};
	});

	$effect(() => {
		if (selectedServerId === null) return;
		if (servers.some((server) => server.server_id === selectedServerId)) return;
		selectedServerId = null;
		mobileSheetOpen = false;
		activeTooltip = null;
	});
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<div class="compact-dashboard">
	<div class="compact-dashboard__list" role="list">
		{#each servers as server (server.server_id)}
			<div role="listitem">
				<CompactServerRow
					{server}
					{showNetwork}
					selected={selectedServerId === server.server_id}
					onSelect={openServer}
					onRegisterRow={registerRow}
					onTooltipChange={updateTooltip}
				/>
			</div>
		{/each}
	</div>

	<aside class="compact-dashboard__detail-panel">
		<CompactServerDetail
			server={selectedServer}
			{showNetwork}
			onClose={closeDesktopDetail}
			titleId="compact-detail-title-desktop"
			mode="panel"
		/>
	</aside>

	{#if activeTooltip}
		<div
			class="compact-dashboard__tooltip"
			role="tooltip"
			style={`left: ${activeTooltip.left}px; top: ${activeTooltip.top}px;`}
		>
			<p class="compact-dashboard__tooltip-title">{activeTooltip.title}</p>
			<ul class="compact-dashboard__tooltip-list">
				{#each activeTooltip.users as user, index (`global-tooltip-${activeTooltip.title}-${user}-${index}`)}
					<li>{user}</li>
				{/each}
			</ul>
			{#if activeTooltip.hiddenUserCount > 0}
				<p class="compact-dashboard__tooltip-meta">+{activeTooltip.hiddenUserCount} hidden in row preview</p>
			{/if}
		</div>
	{/if}
</div>

{#if mobileSheetOpen && selectedServer}
	<div class="compact-sheet-backdrop">
		<button type="button" class="compact-sheet-dismiss" aria-label="상세 닫기" onclick={closeMobileSheet}></button>
		<div class="compact-sheet" role="dialog" tabindex="-1" aria-modal="true" aria-labelledby="compact-detail-title-mobile">
			<CompactServerDetail
				server={selectedServer}
				{showNetwork}
				onClose={closeMobileSheet}
				titleId="compact-detail-title-mobile"
				mode="sheet"
				autofocusClose={true}
			/>
		</div>
	</div>
{/if}
