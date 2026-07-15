<script lang="ts">
	import type { GpuInfo, ServerState, ServerStatus } from '$lib/types';
	import type { CompactGpuState } from '$lib/utils/compactGpuAvailability';
	import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';
	import { compactGpuBankSlots } from '$lib/utils/compactGpuMatrix';

	type CompactHoldCue = {
		owner: string;
		remaining: string;
		memo: string;
	};

	type CompactPopoverItem = {
		gpuIndex: number;
		stateLabel: string;
		ownersLabel: string;
		holdLabel: string;
	};

	type CompactTooltip = {
		serverId: number;
		serverName: string;
		item: CompactPopoverItem;
		left: number;
		top: number;
		trigger: HTMLElement | null;
	};

	let {
		server,
		bankIndex,
		heldGpuIndices = undefined,
		holdCuesByGpu = undefined,
		onOpenFull = () => {},
		onTooltipChange = () => {}
	}: {
		server: ServerState;
		bankIndex: number;
		heldGpuIndices?: ReadonlySet<number>;
		holdCuesByGpu?: ReadonlyMap<number, CompactHoldCue[]>;
		onOpenFull?: (serverId: number) => void;
		onTooltipChange?: (tooltip: CompactTooltip | null) => void;
	} = $props();

	const statusConfig: Record<ServerStatus, { label: string }> = {
		online: { label: '정상' },
		offline: { label: '오프라인' },
		degraded: { label: '지연' },
		unknown: { label: '확인중' }
	};
	const gpuStateLabels: Record<CompactGpuState, string> = {
		available: '사용 가능',
		occupied: '사용 중',
		unknown: '상태 확인 필요'
	};

	const visibleSlots = $derived(compactGpuBankSlots(server.gpus, bankIndex));
	const visibleStatusLabel = $derived(statusConfig[server.status]?.label ?? statusConfig.unknown.label);
	const availableCount = $derived.by(() =>
		visibleSlots.filter((gpu): gpu is GpuInfo => gpu !== null).filter((gpu) => gpuState(gpu) === 'available').length
	);
	const hasAvailable = $derived(availableCount > 0);

	function gpuState(gpu: GpuInfo): CompactGpuState {
		return getCompactGpuState(server.status, server.last_seen, gpu);
	}

	function holdLabel(gpu: GpuInfo): string {
		const cues = holdCuesByGpu?.get(gpu.index) ?? [];
		const fallbackHoldLabel = heldGpuIndices?.has(gpu.index) ? 'HOLD' : '';
		if (cues.length === 0) return fallbackHoldLabel;

		const primaryHold = cues[0];
		const parts = [`HOLD ${primaryHold.owner}`];
		if (primaryHold.remaining) parts.push(primaryHold.remaining);
		if (primaryHold.memo) parts.push(primaryHold.memo);
		if (cues.length > 1) parts.push(`+${cues.length - 1}`);
		return parts.join(' · ');
	}

	function popoverItem(gpu: GpuInfo): CompactPopoverItem {
		const state = gpuState(gpu);
		return {
			gpuIndex: gpu.index,
			stateLabel: gpuStateLabels[state],
			ownersLabel: gpu.users.length > 0 ? gpu.users.join(', ') : 'idle',
			holdLabel: holdLabel(gpu)
		};
	}

	function openTooltip(target: EventTarget | null, item: CompactPopoverItem): void {
		if (!(target instanceof HTMLElement)) return;
		const rect = target.getBoundingClientRect();
		onTooltipChange({
			serverId: server.server_id,
			serverName: server.server_name,
			item,
			left: rect.left + rect.width / 2 - 120,
			top: rect.bottom + 8,
			trigger: target
		});
	}

	function hideTooltip(): void {
		onTooltipChange(null);
	}

	function openFull(): void {
		hideTooltip();
		onOpenFull(server.server_id);
	}

	function handleTriggerBlur(): void {
		hideTooltip();
	}

	function slotAriaLabel(gpu: GpuInfo): string {
		const item = popoverItem(gpu);
		return item.holdLabel
			? `${server.server_name} G${gpu.index}, ${item.stateLabel}, ${item.ownersLabel}, ${item.holdLabel}`
			: `${server.server_name} G${gpu.index}, ${item.stateLabel}, ${item.ownersLabel}`;
	}

	function rowAriaLabel(): string {
		return availableCount > 0
			? `${server.server_name}, ${visibleStatusLabel}, 사용 가능 GPU ${availableCount}개`
			: `${server.server_name}, ${visibleStatusLabel}`;
	}
</script>

<article class="compact-row" class:has-available={hasAvailable}>
	<button
		type="button"
		class="compact-row__select"
		aria-label={rowAriaLabel()}
		data-compact-trigger="true"
		onclick={openFull}
	></button>

	<div class="compact-row__identity">
		<h3 class="compact-row__name">{server.server_name}</h3>
		<span class="compact-row__status" data-status={server.status}>
			<span class="compact-row__status-dot" aria-hidden="true"></span>
			<span class="sr-only">{visibleStatusLabel}</span>
		</span>
	</div>

	{#each visibleSlots as gpu, offset (`compact-slot-${server.server_id}-${bankIndex}-${offset}`)}
		{#if gpu}
			{@const state = gpuState(gpu)}
			<div class="compact-slot" data-state={state} data-held={heldGpuIndices?.has(gpu.index) ? 'true' : undefined}>
				<button
					type="button"
					class="compact-slot__users"
					aria-label={slotAriaLabel(gpu)}
					data-compact-trigger="true"
					onmouseenter={(event) => openTooltip(event.currentTarget, popoverItem(gpu))}
					onmouseleave={hideTooltip}
					onfocus={(event) => openTooltip(event.currentTarget, popoverItem(gpu))}
					onblur={handleTriggerBlur}
					onclick={openFull}
				>
					{#if state === 'available'}
						<span class="compact-slot__free" aria-hidden="true">
							<span class="compact-slot__free-dot"></span>
						</span>
					{:else if state === 'unknown'}
						<span class="compact-slot__unknown" aria-hidden="true">
							<span class="compact-slot__unknown-mark"></span>
						</span>
					{:else}
						<span class="compact-slot__user-list" aria-hidden="true">
							{#each gpu.users as user, index (`${gpu.index}-${user}-${index}`)}
								<span class="compact-slot__username">{user}</span>
							{/each}
						</span>
					{/if}
				</button>
			</div>
		{:else}
			<span class="compact-slot" data-state="absent" aria-hidden="true"></span>
		{/if}
	{/each}
</article>
