<script lang="ts">
	import type { GpuInfo, ServerState, ServerStatus } from '$lib/types';
	import { getLinuxUsernameInitials } from '$lib/utils/linuxUsernameInitials';

	let {
		server,
		selected = false,
		showNetwork = false,
		nowMs,
		onSelect,
		onRegisterRow = () => {}
	}: {
		server: ServerState;
		selected?: boolean;
		showNetwork?: boolean;
		nowMs: number;
		onSelect: (serverId: number) => void;
		onRegisterRow?: (serverId: number, element: HTMLElement | null) => void;
	} = $props();

	let rowEl = $state<HTMLButtonElement | null>(null);

	const statusConfig: Record<ServerStatus, { label: string }> = {
		online: { label: '정상' },
		offline: { label: '오프라인' },
		degraded: { label: '지연' },
		unknown: { label: '확인중' }
	};

	function formatHost(target: ServerState): string {
		return target.port ? `${target.host}:${target.port}` : target.host;
	}

	function formatNetwork(target: ServerState): string {
		return target.network === 'external' ? '외부망' : '내부망';
	}

	function relativeTime(value: string | null): string {
		if (!value) return '업데이트 없음';
		const parsed = Date.parse(value);
		if (Number.isNaN(parsed)) return '업데이트 없음';
		const diffSeconds = Math.max(0, Math.floor((nowMs - parsed) / 1000));
		if (diffSeconds < 60) return `${diffSeconds}초 전`;
		if (diffSeconds < 3600) return `${Math.floor(diffSeconds / 60)}분 전`;
		return `${Math.floor(diffSeconds / 3600)}시간 전`;
	}

	function formatUtilization(value: number): number {
		return Math.round(Math.min(100, Math.max(0, value)));
	}

	function formatMemory(value: number): number {
		return Math.round(value / 1024);
	}

	function slotAriaLabel(gpu: GpuInfo): string {
		const users = gpu.users.length > 0 ? gpu.users.join(', ') : '사용자 없음';
		return `G${gpu.index}, 사용자 ${users}, 사용률 ${formatUtilization(gpu.utilization)} 퍼센트, 메모리 ${formatMemory(gpu.memory_used)} / ${formatMemory(gpu.memory_total)} 기가바이트`;
	}

	function rowAriaLabel(target: ServerState): string {
		const segments = [
			target.server_name,
			statusConfig[target.status]?.label ?? statusConfig.unknown.label,
			formatHost(target),
			relativeTime(target.last_seen)
		];

		if (showNetwork) {
			segments.splice(2, 0, formatNetwork(target));
		}

		return segments.join(' · ');
	}

	function activateRow(): void {
		onSelect(server.server_id);
	}

	function handleKeydown(event: KeyboardEvent): void {
		if (event.currentTarget !== event.target) return;
		if (event.key !== 'Enter' && event.key !== ' ') return;
		event.preventDefault();
		activateRow();
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
		onkeydown={handleKeydown}
	></button>

	<div class="compact-row__identity">
		<div class="compact-row__title-line">
			<h3 class="compact-row__name">{server.server_name}</h3>
			<span class="compact-row__status" data-status={server.status}>
				<span class="compact-row__status-dot" aria-hidden="true"></span>
				<span>{statusConfig[server.status]?.label ?? statusConfig.unknown.label}</span>
			</span>
		</div>

		<p class="compact-row__meta">
			<span class="compact-row__host">{formatHost(server)}</span>
			{#if showNetwork}
				<span aria-hidden="true">•</span>
				<span>{formatNetwork(server)}</span>
			{/if}
			<span aria-hidden="true">•</span>
			<span>{relativeTime(server.last_seen)}</span>
		</p>

		{#if server.status_reason?.message}
			<p class="compact-row__reason">{server.status_reason.message}</p>
		{/if}
	</div>

	<div class="compact-row__slots" aria-label={`${server.server_name} GPU 슬롯`}>
		{#each server.gpus as gpu (gpu.index)}
			{@const visibleUsers = gpu.users.slice(0, 2)}
			{@const hiddenUserCount = Math.max(0, gpu.users.length - 2)}
			{@const tooltipId = `compact-slot-tooltip-${server.server_id}-${gpu.index}`}
			<div class="compact-slot" data-active={gpu.users.length > 0 ? 'true' : 'false'} role="group" aria-label={slotAriaLabel(gpu)}>
				<div class="compact-slot__head">
					<span class="compact-slot__label">G{gpu.index}</span>
					<span class="compact-slot__metric">{formatUtilization(gpu.utilization)}%</span>
				</div>

				<div class="compact-slot__preview">
					{#if gpu.users.length > 0}
						<button
							type="button"
							class="compact-slot__users"
							aria-label={slotAriaLabel(gpu)}
							aria-describedby={tooltipId}
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
							<span class="sr-only">{gpu.users.join(', ')}</span>
						</button>

						<div id={tooltipId} role="tooltip" class="compact-slot__tooltip">
							<p class="compact-slot__tooltip-label">G{gpu.index}</p>
							<ul class="compact-slot__tooltip-list">
								{#each gpu.users as user, index (`tooltip-${gpu.index}-${user}-${index}`)}
									<li>{user}</li>
								{/each}
							</ul>
						</div>
					{:else}
						<span class="compact-slot__empty" aria-hidden="true">
							<span class="compact-slot__empty-dot"></span>
						</span>
						<span class="sr-only">사용 가능한 GPU 슬롯</span>
					{/if}
				</div>
			</div>
		{/each}
	</div>

	<div class="compact-row__chevron" aria-hidden="true">›</div>
</article>
