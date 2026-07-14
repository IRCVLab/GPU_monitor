<script lang="ts">
	import { tick } from 'svelte';
	import type { ServerState, ServerStatus } from '$lib/types';
	import { getLinuxUsernameInitials } from '$lib/utils/linuxUsernameInitials';

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

	function formatHost(target: ServerState): string {
		return target.port ? `${target.host}:${target.port}` : target.host;
	}

	function formatNetwork(target: ServerState): string {
		return target.network === 'external' ? '외부망' : '내부망';
	}

	function absoluteTime(value: string | null): string {
		if (!value) return '업데이트 없음';

		return new Intl.DateTimeFormat('ko-KR', {
			hour: '2-digit',
			minute: '2-digit',
			second: '2-digit',
			hour12: false
		}).format(new Date(value));
	}

	function formatUtilization(value: number): number {
		return Math.round(Math.min(100, Math.max(0, value)));
	}

	function formatMemory(value: number): number {
		return Math.round(value / 1024);
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
				<p class="compact-detail__eyebrow">
					<span>{statusConfig[server.status]?.label ?? statusConfig.unknown.label}</span>
					{#if showNetwork}
						<span aria-hidden="true">•</span>
						<span>{formatNetwork(server)}</span>
					{/if}
				</p>
				<h2 id={titleId} class="compact-detail__title">{server.server_name}</h2>
				<p class="compact-detail__meta">
					<span class="compact-detail__host">{formatHost(server)}</span>
					<span aria-hidden="true">•</span>
					<span>{absoluteTime(server.last_seen)}</span>
				</p>
				{#if server.status_reason?.message}
					<p class="compact-detail__reason">{server.status_reason.message}</p>
				{/if}
			</div>

			<button bind:this={closeButton} type="button" class="compact-detail__close" onclick={onClose}>
				닫기
			</button>
		</div>

		<div class="compact-detail__gpu-list">
			{#each server.gpus as gpu (gpu.index)}
				<section class="compact-detail__gpu" aria-label={`GPU ${gpu.index} 상세`}>
					<div class="compact-detail__gpu-head">
						<div class="compact-detail__gpu-id">
							<span class="compact-detail__gpu-slot">G{gpu.index}</span>
							<span class="compact-detail__gpu-name">{gpu.name}</span>
						</div>
						<div class="compact-detail__gpu-metrics">
							<span>{formatUtilization(gpu.utilization)}%</span>
							<span>{formatMemory(gpu.memory_used)}/{formatMemory(gpu.memory_total)}GB</span>
						</div>
					</div>

					<ul class="compact-detail__users">
						{#if gpu.users.length > 0}
							{#each gpu.users as user, index (`detail-${gpu.index}-${user}-${index}`)}
								{@const badge = getLinuxUsernameInitials(user)}
								<li class="compact-detail__user">
									<span
										class="compact-avatar compact-avatar--detail"
										style={`--compact-avatar-hue: ${badge.seed % 360};`}
									>
										{badge.initials}
									</span>
									<span class="compact-detail__username">{user}</span>
								</li>
							{/each}
						{:else}
							<li class="compact-detail__empty">활성 사용자 없음</li>
						{/if}
					</ul>
				</section>
			{/each}
		</div>
	{:else}
		<div class="compact-detail__placeholder">
			<h2 id={titleId} class="compact-detail__title">Compact 상세</h2>
			<p>서버를 선택하면 GPU 사용자와 점유 상태를 자세히 볼 수 있습니다.</p>
		</div>
	{/if}
</section>
