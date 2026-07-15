<script lang="ts">
	import { browser } from '$app/environment';
	import '$lib/styles/monitor-compact.css';
	import CompactServerRow from '$lib/components/CompactServerRow.svelte';
	import type { ServerState } from '$lib/types';
	import {
		COMPACT_GPU_BANK_SIZE,
		compactGpuBankCount,
		compactGpuBankSlots
	} from '$lib/utils/compactGpuMatrix';

	type CompactPopoverItem = {
		gpuIndex: number;
		users: string[];
	};

	type CompactPopover = {
		serverId: number;
		serverName: string;
		items: CompactPopoverItem[];
		left: number;
		top: number;
	};

	let {
		servers,
		heldGpuIndicesByServer = undefined
	}: {
		servers: ServerState[];
		heldGpuIndicesByServer?: ReadonlyMap<number, ReadonlySet<number>>;
	} = $props();

	const TOOLTIP_WIDTH_ESTIMATE = 220;
	const TOOLTIP_HEIGHT_ESTIMATE = 120;
	const TOOLTIP_VIEWPORT_MARGIN = 12;

	let activeBankIndex = $state(0);
	let activeTooltip = $state<CompactPopover | null>(null);

	const bankCount = $derived(compactGpuBankCount(servers));
	const bankOptions = $derived.by(() => Array.from({ length: bankCount }, (_, index) => index));
	const bankStart = $derived(activeBankIndex * COMPACT_GPU_BANK_SIZE);
	const activeBankHasHardware = $derived.by(() =>
		servers.some((server) => compactGpuBankSlots(server.gpus, activeBankIndex).some(Boolean))
	);

	function clampTooltip(next: CompactPopover): CompactPopover {
		const left = Math.min(
			Math.max(next.left, TOOLTIP_VIEWPORT_MARGIN),
			window.innerWidth - TOOLTIP_WIDTH_ESTIMATE - TOOLTIP_VIEWPORT_MARGIN
		);
		const top = Math.min(
			Math.max(next.top, TOOLTIP_VIEWPORT_MARGIN),
			window.innerHeight - TOOLTIP_HEIGHT_ESTIMATE - TOOLTIP_VIEWPORT_MARGIN
		);
		return { ...next, left, top };
	}

	function updateTooltip(next: CompactPopover | null): void {
		if (!next) {
			activeTooltip = null;
			return;
		}
		activeTooltip = clampTooltip(next);
	}

	function closeTooltip(): void {
		activeTooltip = null;
	}

	function selectBank(index: number): void {
		activeBankIndex = index;
		closeTooltip();
	}

	function handleWindowKeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape' || !activeTooltip) return;
		event.preventDefault();
		closeTooltip();
	}

	$effect(() => {
		if (activeBankIndex < bankCount) return;
		activeBankIndex = Math.max(0, bankCount - 1);
	});

	$effect(() => {
		if (!browser) return;

		const closeOnViewportChange = (): void => {
			closeTooltip();
		};
		const closeOnOutsidePointer = (event: PointerEvent): void => {
			if (!(event.target instanceof HTMLElement)) return;
			if (event.target.closest('[data-compact-popover="true"]')) return;
			if (event.target.closest('[data-compact-trigger="true"]')) return;
			closeTooltip();
		};

		window.addEventListener('scroll', closeOnViewportChange, true);
		window.addEventListener('resize', closeOnViewportChange);
		window.addEventListener('pointerdown', closeOnOutsidePointer);
		return () => {
			window.removeEventListener('scroll', closeOnViewportChange, true);
			window.removeEventListener('resize', closeOnViewportChange);
			window.removeEventListener('pointerdown', closeOnOutsidePointer);
		};
	});
</script>

<svelte:window onkeydown={handleWindowKeydown} />

<section class="compact-dashboard" aria-label="Compact GPU availability rack">
	{#if bankCount > 1}
		<div class="compact-dashboard__controls">
			<div class="compact-dashboard__bank-selector" role="tablist" aria-label="GPU bank selector">
				{#each bankOptions as bankIndex (bankIndex)}
					<button
						type="button"
						class="compact-dashboard__bank-button"
						class:is-active={bankIndex === activeBankIndex}
						role="tab"
						aria-selected={bankIndex === activeBankIndex}
						onclick={() => selectBank(bankIndex)}
					>
						G{bankIndex * COMPACT_GPU_BANK_SIZE}-G{bankIndex * COMPACT_GPU_BANK_SIZE + COMPACT_GPU_BANK_SIZE - 1}
					</button>
				{/each}
			</div>
		</div>
	{/if}

	<div class="compact-dashboard__board" data-empty-bank={activeBankHasHardware ? 'false' : 'true'} role="list">
		<div class="compact-dashboard__column-header" aria-hidden="true">
			<span class="compact-dashboard__column-label">Server</span>
			{#each Array.from({ length: COMPACT_GPU_BANK_SIZE }) as _, offset (`compact-column-${bankStart + offset}`)}
				<span>G{bankStart + offset}</span>
			{/each}
		</div>

		{#each servers as server (server.server_id)}
			<CompactServerRow
				{server}
				bankIndex={activeBankIndex}
				heldGpuIndices={heldGpuIndicesByServer?.get(server.server_id)}
				onTooltipChange={updateTooltip}
			/>
		{/each}
	</div>

	{#if activeTooltip}
		<div
			class="compact-dashboard__tooltip"
			data-compact-popover="true"
			role="tooltip"
			style={`left: ${activeTooltip.left}px; top: ${activeTooltip.top}px;`}
		>
			<p class="compact-dashboard__tooltip-title">{activeTooltip.serverName}</p>
			<ul class="compact-dashboard__tooltip-list">
				{#each activeTooltip.items as item (`tooltip-${activeTooltip.serverId}-${item.gpuIndex}`)}
					<li class="compact-dashboard__tooltip-entry">
						<span class="compact-dashboard__tooltip-gpu">G{item.gpuIndex}</span>
						<span class="compact-dashboard__tooltip-state">사용 중</span>
						<span class="compact-dashboard__tooltip-users">{item.users.join(', ')}</span>
					</li>
				{/each}
			</ul>
		</div>
	{/if}
</section>
