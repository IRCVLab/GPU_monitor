<script lang="ts">
	import { tick } from 'svelte';
	import type { GpuInfo, ServerState, ServerStatus } from '$lib/types';
	import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';

	let {
		server,
		showNetwork = false,
		onClose = () => {},
		titleId = 'compact-detail-title',
		mode = 'panel',
		autofocusClose = false
	}: {
		server: ServerState | null;
		showNetwork?: boolean;
		onClose?: () => void;
		titleId?: string;
		mode?: 'panel' | 'sheet';
		autofocusClose?: boolean;
	} = $props();

	let closeButton = $state<HTMLButtonElement | null>(null);

	const statusConfig: Record<ServerStatus, { label: string }> = {
		online: { label: '정상' },
		offline: { label: '오프라인' },
		degraded: { label: '지연' },
		unknown: { label: '확인중' }
	};

	const sortedGpus = $derived(server ? [...server.gpus].sort((a, b) => a.index - b.index) : []);

	function formatNetwork(target: ServerState): string {
		return target.network === 'external' ? '외부망' : '내부망';
	}


	function occupancyText(gpu: GpuInfo): string {
		const state = server ? getCompactGpuState(server.status, server.last_seen, gpu) : 'unknown';
		if (state === 'available') return 'Available';
		if (state === 'unknown') return '상태 확인 필요';
		return gpu.users.join(', ');
	}

	$effect(() => {
		if (!autofocusClose || !server) return;
		void tick().then(() => closeButton?.focus());
	});
</script>

<section class={`compact-detail compact-detail--${mode}`} aria-labelledby={titleId}>
	{#if server}
		<div class="compact-detail__header">
			<div class="compact-detail__identity">
				<div class="compact-detail__title-line">
					<h2 id={titleId} class="compact-detail__title">{server.server_name}</h2>
					<span class="compact-detail__status" data-status={server.status}>
						<span class="compact-detail__status-dot" aria-hidden="true"></span>
						<span>{statusConfig[server.status]?.label ?? statusConfig.unknown.label}</span>
					</span>
					{#if showNetwork}
						<span class="compact-detail__network">{formatNetwork(server)}</span>
					{/if}
				</div>
			</div>

			<button bind:this={closeButton} type="button" class="compact-detail__close" onclick={onClose}>
				닫기
			</button>
		</div>

		<div class="compact-detail__gpu-list">
			{#each sortedGpus as gpu (gpu.index)}
				<div class="compact-detail__gpu" data-state={server ? getCompactGpuState(server.status, server.last_seen, gpu) : 'unknown'} aria-label={`G${gpu.index}, ${occupancyText(gpu)}`}>
					<span class="compact-detail__gpu-slot">G{gpu.index}</span>
					<span class="compact-detail__gpu-occupancy">{occupancyText(gpu)}</span>
				</div>
			{/each}
		</div>
	{:else}
		<div class="compact-detail__placeholder">
			<h2 id={titleId} class="compact-detail__title">Compact occupancy</h2>
			<p>서버를 선택하면 점유 사용자만 빠르게 확인할 수 있습니다.</p>
		</div>
	{/if}
</section>
