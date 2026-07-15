<script lang="ts">
	import type { GpuInfo, ServerState, ServerStatus } from '$lib/types';
	import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';
	import { compactGpuBankSlots } from '$lib/utils/compactGpuMatrix';
	import { getLinuxUsernameInitials } from '$lib/utils/linuxUsernameInitials';

	type CompactPopoverItem = {
		gpuIndex: number;
		users: string[];
	};

	type CompactTooltip = {
		serverId: number;
		serverName: string;
		items: CompactPopoverItem[];
		left: number;
		top: number;
	};

	let {
		server,
		bankIndex,
		heldGpuIndices = undefined,
		onOpenFull = () => {},
		onTooltipChange = () => {}
	}: {
		server: ServerState;
		bankIndex: number;
		heldGpuIndices?: ReadonlySet<number>;
		onOpenFull?: (serverId: number) => void;
		onTooltipChange?: (tooltip: CompactTooltip | null) => void;
	} = $props();


	const statusConfig: Record<ServerStatus, { label: string }> = {
		online: { label: '정상' },
		offline: { label: '오프라인' },
		degraded: { label: '지연' },
		unknown: { label: '확인중' }
	};

	const visibleSlots = $derived(compactGpuBankSlots(server.gpus, bankIndex));
	const visibleStatusLabel = $derived(statusConfig[server.status]?.label ?? statusConfig.unknown.label);
	const availableCount = $derived.by(() =>
		visibleSlots.filter((gpu): gpu is GpuInfo => gpu !== null).filter((gpu) => gpuState(gpu) === 'available').length
	);
	const hasAvailable = $derived(availableCount > 0);
	const occupiedSlots = $derived.by(() =>
		visibleSlots
			.filter((gpu): gpu is GpuInfo => gpu !== null)
			.filter((gpu) => gpuState(gpu) === 'occupied')
	);

	function gpuState(gpu: GpuInfo) {
		return getCompactGpuState(server.status, server.last_seen, gpu);
	}

	function popoverItems(gpus: readonly GpuInfo[]): CompactPopoverItem[] {
		return gpus.map((gpu) => ({
			gpuIndex: gpu.index,
			users: [...gpu.users]
		}));
	}

	function openTooltip(target: EventTarget | null, items: CompactPopoverItem[]): void {
		if (!(target instanceof HTMLElement) || items.length === 0) return;
		const rect = target.getBoundingClientRect();
		onTooltipChange({
			serverId: server.server_id,
			serverName: server.server_name,
			items,
			left: rect.left + rect.width / 2 - 110,
			top: rect.bottom + 8
		});
	}

	function hideTooltip(): void {
		onTooltipChange(null);
	}

	function openFull(): void {
		hideTooltip();
		onOpenFull(server.server_id);
	}

	function handleSlotClick(event: MouseEvent, gpu: GpuInfo): void {
		event.stopPropagation();
		openTooltip(event.currentTarget, popoverItems([gpu]));
	}

	function slotAriaLabel(gpu: GpuInfo): string {
		const state = gpuState(gpu);
		if (state === 'available') return `${server.server_name} G${gpu.index}, 사용 가능`;
		if (state === 'unknown') return `${server.server_name} G${gpu.index}, 상태 확인 필요`;
		return `${server.server_name} G${gpu.index}, 사용자 ${gpu.users.join(', ')}`;
	}

	function rowAriaLabel(): string {
		return availableCount > 0
			? `${server.server_name}, ${visibleStatusLabel}, 사용 가능 GPU ${availableCount}개`
			: `${server.server_name}, ${visibleStatusLabel}`;
	}

	function occupantPreview(gpu: GpuInfo): { initials: string[]; hiddenUserCount: number } {
		return {
			initials: gpu.users.slice(0, 2).map((user) => getLinuxUsernameInitials(user).initials),
			hiddenUserCount: Math.max(0, gpu.users.length - 2)
		};
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
			{@const preview = occupantPreview(gpu)}
			<div
				class="compact-slot"
				data-state={state}
				data-held={heldGpuIndices?.has(gpu.index) ? 'true' : undefined}
				aria-label={slotAriaLabel(gpu)}
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
					<button
						type="button"
						class="compact-slot__users"
						aria-label={slotAriaLabel(gpu)}
						data-compact-trigger="true"
						onmouseenter={(event) => openTooltip(event.currentTarget, popoverItems([gpu]))}
						onmouseleave={hideTooltip}
						onfocus={(event) => openTooltip(event.currentTarget, popoverItems([gpu]))}
						onblur={hideTooltip}
						onclick={(event) => handleSlotClick(event, gpu)}
					>
						<span class="compact-slot__occupants" aria-hidden="true">
							{#each preview.initials as initials, index (`${gpu.index}-${initials}-${index}`)}
								<span class="compact-slot__badge">{initials}</span>
							{/each}
							{#if preview.hiddenUserCount > 0}
								<span class="compact-slot__badge compact-slot__badge--count">+{preview.hiddenUserCount}</span>
							{/if}
						</span>
					</button>
				{/if}
			</div>
		{:else}
			<span class="compact-slot" data-state="absent" aria-hidden="true"></span>
		{/if}
	{/each}
</article>
