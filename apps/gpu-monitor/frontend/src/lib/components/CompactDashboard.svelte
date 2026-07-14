<script lang="ts">
	import { browser } from '$app/environment';
	import { tick } from 'svelte';
	import '$lib/styles/monitor-compact.css';
	import type { ServerState } from '$lib/types';
	import CompactServerRow from '$lib/components/CompactServerRow.svelte';
	import CompactServerDetail from '$lib/components/CompactServerDetail.svelte';

	let {
		servers,
		showNetwork = false,
		nowMs
	}: {
		servers: ServerState[];
		showNetwork?: boolean;
		nowMs: number;
	} = $props();

	const DESKTOP_MEDIA_QUERY = '(min-width: 1200px)';

	let selectedServerId = $state<number | null>(null);
	let mobileSheetOpen = $state(false);
	let lastTriggerRowId = $state<number | null>(null);
	let isDesktop = $state(false);
	let rowRefs = new Map<number, HTMLElement>();

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

	function closeMobileSheet(): void {
		mobileSheetOpen = false;
		closeSelection();
		void restoreFocus();
	}

	function handleWindowKeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape' || !mobileSheetOpen) return;
		event.preventDefault();
		closeMobileSheet();
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
		return () => {
			media.removeEventListener('change', updateLayout);
		};
	});

	$effect(() => {
		if (selectedServerId === null) return;
		if (servers.some((server) => server.server_id === selectedServerId)) return;
		selectedServerId = null;
		mobileSheetOpen = false;
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
					{nowMs}
					selected={selectedServerId === server.server_id}
					onSelect={openServer}
					onRegisterRow={registerRow}
				/>
			</div>
		{/each}
	</div>

	<aside class="compact-dashboard__detail-panel">
		<CompactServerDetail
			server={selectedServer}
			{showNetwork}
			onClose={closeSelection}
			titleId="compact-detail-title-desktop"
			mode="panel"
		/>
	</aside>
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
