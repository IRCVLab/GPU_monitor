<script lang="ts">
	import { tick } from 'svelte';
	import type { GpuInfo, ServerState, ServerStatus } from '$lib/types';
	import { getCompactGpuState } from '$lib/utils/compactGpuAvailability';

	let {
		server,
		onClose = () => {},
		titleId = 'compact-detail-title',
		mode = 'overlay',
		autofocusClose = false
	}: {
		server: ServerState | null;
		onClose?: () => void;
		titleId?: string;
		mode?: 'overlay' | 'sheet';
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


	function occupancyText(gpu: GpuInfo): string {
		const state = server ? getCompactGpuState(server.status, server.last_seen, gpu) : 'unknown';
		if (state === 'available') return '사용 가능';
		if (state === 'unknown') return '상태 확인 필요';
		return gpu.users.join(', ');
	}

	$effect(() => {
		if (!autofocusClose || !server) return;
		void tick().then(() => closeButton?.focus());
	});
</script>

{#if server}
	<section class={`compact-detail compact-detail--${mode}`} aria-labelledby={titleId}>
		<div class="compact-detail__header">
			<div class="compact-detail__identity">
				<div class="compact-detail__title-line">
					<h2 id={titleId} class="compact-detail__title">{server.server_name}</h2>
					<span class="compact-detail__status" data-status={server.status}>
						<span class="compact-detail__status-dot" aria-hidden="true"></span>
						<span>{statusConfig[server.status]?.label ?? statusConfig.unknown.label}</span>
					</span>
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
	</section>
{/if}
