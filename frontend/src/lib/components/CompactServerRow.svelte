<script lang="ts">
	import { cubicOut } from 'svelte/easing';
	import { prefersReducedMotion } from 'svelte/motion';
	import { fly } from 'svelte/transition';
	import type { GpuInfo, Note, ServerState, ServerStatus } from '$lib/types';
	import type { CompactGpuState } from '$lib/utils/compactGpuAvailability';
	import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';
	import { compactGpuBankSlots } from '$lib/utils/compactGpuMatrix';
	import { buildHoldAdvisory, getNotePriorityMeta, resolveDisplayName } from '$lib/utils/noteAdvisory';

	type CompactHoldCue = {
		note: Note;
		remaining: string;
	};

	type CompactTooltipHoldEntry = {
		displayName: string;
		priorityLabel: string;
		priorityClassName: string;
		remaining: string;
		memo: string;
	};

	type CompactPopoverItem = {
		gpuIndex: number;
		stateLabel: string;
		ownersLabel: string;
		holdLabel: string;
		holdEntries: CompactTooltipHoldEntry[];
		tooltipId: string;
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
		activeTooltipId = null,
		onOpenFull = () => {},
		onTooltipChange = () => {}
	}: {
		server: ServerState;
		bankIndex: number;
		heldGpuIndices?: ReadonlySet<number>;
		holdCuesByGpu?: ReadonlyMap<number, CompactHoldCue[]>;
		activeTooltipId?: string | null;
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
	const slotIdentityInFly = $derived({
		y: prefersReducedMotion.current ? 0 : 2,
		opacity: prefersReducedMotion.current ? 1 : 0,
		duration: prefersReducedMotion.current ? 0 : 220,
		easing: cubicOut
	});
	const slotIdentityOutFly = $derived({
		y: prefersReducedMotion.current ? 0 : -2,
		opacity: prefersReducedMotion.current ? 1 : 0,
		duration: prefersReducedMotion.current ? 0 : 160,
		easing: cubicOut
	});

	const visibleSlots = $derived(compactGpuBankSlots(server.gpus, bankIndex));
	const visibleStatusLabel = $derived(statusConfig[server.status]?.label ?? statusConfig.unknown.label);
	const availableCount = $derived.by(() =>
		visibleSlots.filter((gpu): gpu is GpuInfo => gpu !== null).filter((gpu) => gpuState(gpu) === 'available').length
	);
	const hasAvailable = $derived(availableCount > 0);

	function gpuState(gpu: GpuInfo): CompactGpuState {
		return getCompactGpuState(server.status, server.last_seen, gpu);
	}

	function displayUsers(gpu: GpuInfo): string[] {
		return [...gpu.users].sort();
	}

	function displayUsersSignature(users: readonly string[]): string {
		return users.join('\u0000') || 'idle';
	}

	function tooltipId(gpu: GpuInfo): string {
		return `compact-tooltip-${server.server_id}-${gpu.index}`;
	}

	function tooltipVisible(gpu: GpuInfo): boolean {
		return activeTooltipId === tooltipId(gpu);
	}

	function holdCues(gpu: GpuInfo): CompactHoldCue[] {
		return holdCuesByGpu?.get(gpu.index) ?? [];
	}

	function holdNotes(gpu: GpuInfo): Note[] {
		return holdCues(gpu).map(({ note }) => note);
	}

	function orderedHoldEntries(gpu: GpuInfo): CompactHoldCue[] {
		const cues = holdCues(gpu);
		const cueByNote = new Map(cues.map((entry) => [entry.note, entry] as const));
		const holdAdvisory = buildHoldAdvisory(holdNotes(gpu));
		return holdAdvisory.ordered
			.map((note) => cueByNote.get(note))
			.filter((entry): entry is CompactHoldCue => Boolean(entry));
	}

	function holdLabel(gpu: GpuInfo): string {
		const fallbackHoldLabel = heldGpuIndices?.has(gpu.index) ? 'HOLD' : '';
		const holdAdvisory = buildHoldAdvisory(holdNotes(gpu));
		const primaryHold = holdAdvisory.primary;
		if (!primaryHold) return fallbackHoldLabel;

		const primaryPriorityMeta = getNotePriorityMeta(primaryHold.priority);
		const primaryEntry = orderedHoldEntries(gpu)[0] ?? null;
		const parts = ['HOLD'];
		if (primaryHold.priority !== 'normal') {
			parts.push(primaryPriorityMeta.label);
		}
		parts.push(resolveDisplayName(primaryHold));
		if (primaryEntry?.remaining) parts.push(primaryEntry.remaining);
		if (primaryHold.content) parts.push(primaryHold.content);
		if (holdAdvisory.secondarySummary) parts.push(holdAdvisory.secondarySummary);
		return parts.join(' · ');
	}

	function holdEntries(gpu: GpuInfo): CompactTooltipHoldEntry[] {
		return orderedHoldEntries(gpu).map(({ note, remaining }) => {
			const priorityMeta = getNotePriorityMeta(note.priority);
			return {
				displayName: resolveDisplayName(note),
				priorityLabel: priorityMeta.label,
				priorityClassName: priorityMeta.className,
				remaining,
				memo: note.content
			};
		});
	}

	function popoverItem(gpu: GpuInfo, users: readonly string[]): CompactPopoverItem {
		const state = gpuState(gpu);
		return {
			gpuIndex: gpu.index,
			stateLabel: gpuStateLabels[state],
			ownersLabel: users.length > 0 ? users.join(', ') : 'idle',
			holdLabel: holdLabel(gpu),
			holdEntries: holdEntries(gpu),
			tooltipId: tooltipId(gpu)
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

	function slotAriaLabel(gpu: GpuInfo, users: readonly string[]): string {
		const item = popoverItem(gpu, users);
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
			{@const users = displayUsers(gpu)}
			<div class="compact-slot" data-state={state} data-held={heldGpuIndices?.has(gpu.index) ? 'true' : undefined}>
				<button
					type="button"
					class="compact-slot__users"
					aria-label={slotAriaLabel(gpu, users)}
					aria-describedby={tooltipVisible(gpu) ? tooltipId(gpu) : undefined}
					data-compact-trigger="true"
					onmouseenter={(event) => openTooltip(event.currentTarget, popoverItem(gpu, users))}
					onmouseleave={hideTooltip}
					onfocus={(event) => openTooltip(event.currentTarget, popoverItem(gpu, users))}
					onblur={handleTriggerBlur}
					onclick={openFull}
				>
					<div class="compact-slot__identity-slot" aria-hidden="true">
						{#key `${gpu.index}:${state}:${displayUsersSignature(users)}`}
							<span class="compact-slot__identity-set" data-state={state} in:fly={slotIdentityInFly} out:fly={slotIdentityOutFly}>
								{#if state === 'available'}
									<span class="compact-slot__free">
										<span class="compact-slot__free-dot"></span>
									</span>
								{:else if state === 'unknown'}
									<span class="compact-slot__unknown">
										<span class="compact-slot__unknown-mark"></span>
									</span>
								{:else}
									<span class="compact-slot__user-list">
										{#each users as user, index (`${gpu.index}-${user}-${index}`)}
											<span class="compact-slot__username">{user}</span>
										{/each}
									</span>
								{/if}
							</span>
						{/key}
					</div>
				</button>
			</div>
		{:else}
			<span class="compact-slot" data-state="absent" aria-hidden="true"></span>
		{/if}
	{/each}
</article>
