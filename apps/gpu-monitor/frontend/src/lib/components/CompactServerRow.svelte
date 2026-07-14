<script lang="ts">
	import type { GpuInfo, ServerState, ServerStatus } from '$lib/types';
	import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';
	import { getLinuxUsernameInitials } from '$lib/utils/linuxUsernameInitials';

	type CompactTooltip = {
		title: string;
		users: string[];
		hiddenUserCount: number;
		left: number;
		top: number;
	};

	let {
		server,
		selected = false,
		onSelect,
		onRegisterRow = () => {},
		onTooltipChange = () => {}
	}: {
		server: ServerState;
		selected?: boolean;
		onSelect: (serverId: number) => void;
		onRegisterRow?: (serverId: number, element: HTMLElement | null) => void;
		onTooltipChange?: (tooltip: CompactTooltip | null) => void;
	} = $props();

	let rowEl = $state<HTMLButtonElement | null>(null);

	const statusConfig: Record<ServerStatus, { label: string }> = {
		online: { label: '정상' },
		offline: { label: '오프라인' },
		degraded: { label: '지연' },
		unknown: { label: '확인중' }
	};

	const sortedGpus = $derived([...server.gpus].sort((a, b) => a.index - b.index));

	function hiddenUserText(gpu: GpuInfo): string {
		const hiddenUserCount = Math.max(0, gpu.users.length - 2);
		return hiddenUserCount > 0 ? `, 미리보기 외 추가 사용자 ${hiddenUserCount}명` : '';
	}

	function slotAriaLabel(gpu: GpuInfo): string {
		const state = getCompactGpuState(server.status, server.last_seen, gpu);
		if (state === 'available') {
			return `G${gpu.index}, 사용 가능`;
		}
		if (state === 'unknown') {
			return `G${gpu.index}, 상태 확인 필요`;
		}
		return `G${gpu.index}, 사용자 ${gpu.users.join(', ')}${hiddenUserText(gpu)}`;
	}

	function rowAriaLabel(target: ServerState): string {
		const segments = [
			target.server_name,
			statusConfig[target.status]?.label ?? statusConfig.unknown.label
		];

		return segments.join(' · ');
	}

	function activateRow(): void {
		onSelect(server.server_id);
	}

	function showTooltip(gpu: GpuInfo, target: EventTarget | null): void {
		if (!(target instanceof HTMLElement)) return;
		const rect = target.getBoundingClientRect();
		onTooltipChange({
			title: `G${gpu.index}`,
			users: [...gpu.users],
			hiddenUserCount: Math.max(0, gpu.users.length - 2),
			left: rect.left + rect.width / 2 - 88,
			top: rect.bottom + 8
		});
	}

	function hideTooltip(): void {
		onTooltipChange(null);
	}

	$effect(() => {
		onRegisterRow(server.server_id, rowEl);
		return () => {
			onRegisterRow(server.server_id, null);
		};
	});
</script>

<article class="compact-row" class:is-selected={selected}>
	<button
		bind:this={rowEl}
		type="button"
		class="compact-row__select"
		aria-pressed={selected}
		aria-label={rowAriaLabel(server)}
		onclick={activateRow}
	></button>

	<div class="compact-row__identity">
		<div class="compact-row__title-line">
			<h3 class="compact-row__name">{server.server_name}</h3>
			<span class="compact-row__status" data-status={server.status}>
				<span class="compact-row__status-dot" aria-hidden="true"></span>
				<span>{statusConfig[server.status]?.label ?? statusConfig.unknown.label}</span>
			</span>
		</div>
	</div>

	<div class="compact-row__slots" aria-label={`${server.server_name} GPU 슬롯`}>
		{#each sortedGpus as gpu (gpu.index)}
			{@const visibleUsers = gpu.users.slice(0, 2)}
			{@const hiddenUserCount = Math.max(0, gpu.users.length - 2)}
			{@const state = getCompactGpuState(server.status, server.last_seen, gpu)}
			<div
				class="compact-slot"
				data-state={state}
				data-available={state === 'available' ? 'true' : 'false'}
				role="group"
				aria-label={slotAriaLabel(gpu)}
			>
				<span class="compact-slot__label">G{gpu.index}</span>

				<div class="compact-slot__preview">
					{#if state === 'available'}
						<span class="compact-slot__free" aria-hidden="true">
							<span class="compact-slot__free-dot"></span>
						</span>
						<span class="sr-only">사용 가능한 GPU 슬롯</span>
					{:else if state === 'unknown'}
						<span class="compact-slot__unknown" aria-hidden="true">
							<span class="compact-slot__unknown-dot"></span>
						</span>
						<span class="sr-only">상태 확인이 필요한 GPU 슬롯</span>
					{:else}
						<button
							type="button"
							class="compact-slot__users"
							aria-label={slotAriaLabel(gpu)}
							onmouseenter={(event) => showTooltip(gpu, event.currentTarget)}
							onmouseleave={hideTooltip}
							onfocus={(event) => showTooltip(gpu, event.currentTarget)}
							onblur={hideTooltip}
							onclick={activateRow}
						>
							<span class="compact-avatar-stack" aria-hidden="true">
								{#each visibleUsers as user, index (`slot-${gpu.index}-${user}-${index}`)}
									{@const badge = getLinuxUsernameInitials(user)}
									<span
										class="compact-avatar"
										style={`--compact-avatar-hue: ${badge.seed % 360};`}
									>
										{badge.initials}
									</span>
								{/each}
								{#if hiddenUserCount > 0}
									<span class="compact-avatar-count">+{hiddenUserCount}</span>
								{/if}
							</span>
							<span class="sr-only">
								{gpu.users.join(', ')}{hiddenUserCount > 0 ? `. 미리보기 외 추가 사용자 ${hiddenUserCount}명.` : ''}
							</span>
						</button>
					{/if}
				</div>
			</div>
		{/each}
	</div>
</article>
