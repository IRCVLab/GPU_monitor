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
		servers
	}: {
		servers: ServerState[];
	} = $props();

	const DESKTOP_MEDIA_QUERY = '(min-width: 1200px)';
	const TOOLTIP_WIDTH_ESTIMATE = 220;
	const TOOLTIP_VIEWPORT_MARGIN = 12;

	let selectedServerId = $state<number | null>(null);
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
	}

	async function restoreFocus(): Promise<void> {
		if (lastTriggerRowId === null) return;
		await tick();
		rowRefs.get(lastTriggerRowId)?.focus();
	}

	function closeSelection(): void {
		activeTooltip = null;
		selectedServerId = null;
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
		if (selectedServerId !== null) {
			event.preventDefault();
			closeSelection();
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
					selected={selectedServerId === server.server_id}
					onSelect={openServer}
					onRegisterRow={registerRow}
					onTooltipChange={updateTooltip}
				/>
			</div>
		{/each}
	</div>

	{#if selectedServer && isDesktop}
		<div class="compact-detail-overlay">
			<CompactServerDetail
				server={selectedServer}
				mode="overlay"
				titleId="compact-detail-title-desktop"
				onClose={closeSelection}
			/>
		</div>
	{/if}

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

{#if selectedServer && !isDesktop}
	<div class="compact-sheet-backdrop">
		<button type="button" class="compact-sheet-dismiss" aria-label="상세 닫기" onclick={closeSelection}></button>
		<div class="compact-sheet" role="dialog" tabindex="-1" aria-modal="true" aria-labelledby="compact-detail-title-mobile">
			<CompactServerDetail
				server={selectedServer}
				mode="sheet"
				titleId="compact-detail-title-mobile"
				autofocusClose={true}
				onClose={closeSelection}
			/>
		</div>
	</div>
{/if}
