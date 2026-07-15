<script lang="ts">
	import { browser } from '$app/environment';
	import { tick } from 'svelte';
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
		trigger: HTMLElement | null;
		focusAction: boolean;
	};

	let {
		servers,
		heldGpuIndicesByServer = undefined,
		onOpenFull = () => {}
	}: {
		servers: ServerState[];
		heldGpuIndicesByServer?: ReadonlyMap<number, ReadonlySet<number>>;
		onOpenFull?: (serverId: number) => void;
	} = $props();

	const TOOLTIP_WIDTH_ESTIMATE = 220;
	const TOOLTIP_HEIGHT_ESTIMATE = 120;
	const TOOLTIP_VIEWPORT_MARGIN = 12;

	let activeBankIndex = $state(0);
	let activeTooltip = $state<CompactPopover | null>(null);
	let tooltipActionButton = $state<HTMLButtonElement | null>(null);

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

	function closeTooltip({ restoreFocus = false }: { restoreFocus?: boolean } = {}): void {
		const currentTooltip = activeTooltip;
		activeTooltip = null;
		if (restoreFocus && currentTooltip?.trigger?.isConnected) {
			currentTooltip.trigger.focus();
		}
	}

	function openFull(serverId: number): void {
		closeTooltip();
		onOpenFull(serverId);
	}

	function selectBank(index: number): void {
		activeBankIndex = index;
		closeTooltip();
	}

	function handleWindowKeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape' || !activeTooltip) return;
		event.preventDefault();
		closeTooltip({ restoreFocus: true });
	}

	function handlePopoverFocusOut(event: FocusEvent): void {
		const nextTarget = event.relatedTarget;
		if (
			nextTarget instanceof Node &&
			event.currentTarget instanceof HTMLElement &&
			event.currentTarget.contains(nextTarget)
		) {
			return;
		}
		closeTooltip();
	}

	$effect(() => {
		if (activeBankIndex < bankCount) return;
		activeBankIndex = Math.max(0, bankCount - 1);
	});

	$effect(() => {
		if (!browser || !activeTooltip?.focusAction) return;
		void tick().then(() => tooltipActionButton?.focus());
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
			<div class="compact-dashboard__bank-selector" role="group" aria-label="GPU bank selector">
				{#each bankOptions as bankIndex (bankIndex)}
					<button
						type="button"
						class="compact-dashboard__bank-button"
						class:is-active={bankIndex === activeBankIndex}
						aria-pressed={bankIndex === activeBankIndex}
						aria-current={bankIndex === activeBankIndex ? 'true' : undefined}
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
				onOpenFull={openFull}
				onTooltipChange={updateTooltip}
			/>
		{/each}
	</div>

	{#if activeTooltip}
		{@const tooltipServerId = activeTooltip.serverId}
		<div
			class="compact-dashboard__tooltip"
			data-compact-popover="true"
			role="dialog"
			aria-modal="false"
			aria-label={`${activeTooltip.serverName} GPU 점유 정보`}
			style={`left: ${activeTooltip.left}px; top: ${activeTooltip.top}px;`}
			onfocusout={handlePopoverFocusOut}
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
			<div class="compact-dashboard__tooltip-footer">
				<button
					type="button"
					class="compact-dashboard__tooltip-action"
					bind:this={tooltipActionButton}
					onclick={() => openFull(tooltipServerId)}
				>Full에서 보기</button>
			</div>
		</div>
	{/if}
</section>
