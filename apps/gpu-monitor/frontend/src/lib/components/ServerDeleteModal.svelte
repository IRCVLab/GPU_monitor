<script lang="ts">
	import type { ServerState } from '$lib/types';
	import { deleteServer } from '$lib/api';
	import { internalServers, externalServers } from '$lib/stores/servers';
	import StatusBadge from '$lib/components/StatusBadge.svelte';

	let {
		open,
		onClose,
		onDeleted
	}: {
		open: boolean;
		onClose: () => void;
		onDeleted: () => void;
	} = $props();

	type Step = 1 | 2 | 3;

	let step           = $state<Step>(1);
	let adminPassword  = $state('');
	let passwordError  = $state('');
	let selectedServer = $state<ServerState | null>(null);
	let deleting       = $state(false);
	let deleteError    = $state('');

	// Reset on open/close
	$effect(() => {
		if (open) {
			step           = 1;
			adminPassword  = '';
			passwordError  = '';
			selectedServer = null;
			deleting       = false;
			deleteError    = '';
		}
	});

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') onClose();
	}

	function handleStep1() {
		if (!adminPassword.trim()) {
			passwordError = '비밀번호를 입력하세요.';
			return;
		}
		passwordError = '';
		step = 2;
	}

	function handleStep2() {
		if (!selectedServer) return;
		step = 3;
	}

	async function handleDelete() {
		if (!selectedServer) return;
		deleting     = true;
		deleteError  = '';
		try {
			await deleteServer(selectedServer.server_id, adminPassword.trim());
			onDeleted();
			onClose();
		} catch (e) {
			deleteError = e instanceof Error ? e.message : '삭제 실패';
		} finally {
			deleting = false;
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
	<!-- Backdrop / overlay -->
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="modal-overlay"
		onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}
		role="dialog"
		aria-modal="true"
		tabindex="-1"
	>
		<div class="modal-card">

			<!-- Modal header -->
			<div class="flex items-center justify-between mb-1 pb-4 border-b border-surface-border">
				<div class="flex items-center gap-2">
					<!-- Step icon -->
					{#if step === 3}
						<div class="w-7 h-7 rounded-lg bg-red-500/10 border border-red-500/20 flex items-center justify-center shrink-0">
							<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 text-red-400/70" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
								<line x1="12" y1="9" x2="12" y2="13"/>
								<line x1="12" y1="17" x2="12.01" y2="17"/>
							</svg>
						</div>
					{:else}
						<div class="w-7 h-7 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center shrink-0">
							<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5 text-white/40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
								<polyline points="3 6 5 6 21 6"/>
								<path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
								<path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
							</svg>
						</div>
					{/if}
					<h2 class="text-sm font-semibold text-white/90">서버 삭제</h2>
				</div>

				<div class="flex items-center gap-3">
					<!-- Step progress dots -->
					<div class="flex items-center gap-1">
						{#each [1, 2, 3] as s}
							<div class="rounded-full transition-all duration-200
								{step === s
									? 'w-3.5 h-1.5 bg-white/50'
									: step > s
										? 'w-1.5 h-1.5 bg-white/30'
										: 'w-1.5 h-1.5 bg-white/10'}">
							</div>
						{/each}
					</div>
					<button
						onclick={onClose}
						class="w-6 h-6 flex items-center justify-center rounded text-white/30 hover:text-white/70 hover:bg-white/10 transition-colors"
						aria-label="닫기"
					>
						<svg xmlns="http://www.w3.org/2000/svg" class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
							<line x1="18" y1="6" x2="6" y2="18"/>
							<line x1="6" y1="6" x2="18" y2="18"/>
						</svg>
					</button>
				</div>
			</div>

			<!-- Step content -->
			<div class="pt-4">

				{#if step === 1}
					<!-- ── Step 1: Password ── -->
					<p class="text-xs text-white/40 mb-4">서버를 삭제하려면 관리자 비밀번호를 입력하세요.</p>
					<label class="block text-[10px] text-white/30 uppercase tracking-wider mb-1.5" for="del-admin-pw">
						관리자 비밀번호
					</label>
					<input
						id="del-admin-pw"
						type="password"
						placeholder="••••••••••"
						bind:value={adminPassword}
						onkeydown={(e) => { if (e.key === 'Enter') handleStep1(); }}
						class="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm w-full
						       focus:outline-none focus:border-white/25 transition-colors
						       text-white/80 placeholder:text-white/20"
					/>
					{#if passwordError}
						<p class="text-xs text-red-400/80 mt-1.5">{passwordError}</p>
					{/if}
					<div class="flex items-center justify-end gap-2 mt-5">
						<button onclick={onClose} class="btn-ghost text-xs">취소</button>
						<button
							onclick={handleStep1}
							class="btn text-xs bg-white/10 text-white/80 hover:bg-white/15 flex items-center gap-1.5 transition-colors"
						>
							다음
							<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
								<polyline points="9 18 15 12 9 6"/>
							</svg>
						</button>
					</div>

				{:else if step === 2}
					<!-- ── Step 2: Server selection ── -->
					<p class="text-xs text-white/40 mb-3">삭제할 서버를 선택하세요.</p>

					<div class="rounded-lg border border-white/8 overflow-hidden">
						{#if $internalServers.length > 0}
							<p class="section-label border-b border-white/5">내부망</p>
							{#each $internalServers as server}
								<button
									onclick={() => (selectedServer = server)}
									class="w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors
										{selectedServer?.server_id === server.server_id
											? 'bg-white/8'
											: 'hover:bg-white/5'}"
								>
									<!-- Radio indicator -->
									<span class="w-3.5 h-3.5 rounded-full border flex-shrink-0 flex items-center justify-center
										{selectedServer?.server_id === server.server_id
											? 'border-white/60 bg-white/15'
											: 'border-white/20'}">
										{#if selectedServer?.server_id === server.server_id}
											<span class="w-1.5 h-1.5 rounded-full bg-white/80"></span>
										{/if}
									</span>
									<div class="flex-1 min-w-0">
										<span class="text-xs text-white/80 font-medium">{server.server_name}</span>
										<span class="text-[10px] text-white/30 font-mono ml-2">{server.host}</span>
									</div>
									<StatusBadge status={server.status} />
								</button>
							{/each}
						{/if}

						{#if $externalServers.length > 0}
							<p class="section-label border-b border-white/5 {$internalServers.length > 0 ? 'border-t border-white/5' : ''}">외부망</p>
							{#each $externalServers as server}
								<button
									onclick={() => (selectedServer = server)}
									class="w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors
										{selectedServer?.server_id === server.server_id
											? 'bg-white/8'
											: 'hover:bg-white/5'}"
								>
									<span class="w-3.5 h-3.5 rounded-full border flex-shrink-0 flex items-center justify-center
										{selectedServer?.server_id === server.server_id
											? 'border-white/60 bg-white/15'
											: 'border-white/20'}">
										{#if selectedServer?.server_id === server.server_id}
											<span class="w-1.5 h-1.5 rounded-full bg-white/80"></span>
										{/if}
									</span>
									<div class="flex-1 min-w-0">
										<span class="text-xs text-white/80 font-medium">{server.server_name}</span>
										<span class="text-[10px] text-white/30 font-mono ml-2">{server.host}</span>
									</div>
									<StatusBadge status={server.status} />
								</button>
							{/each}
						{/if}

						{#if $internalServers.length === 0 && $externalServers.length === 0}
							<p class="text-xs text-white/25 py-6 text-center">등록된 서버가 없습니다.</p>
						{/if}
					</div>

					<div class="flex items-center justify-between mt-5">
						<button onclick={() => (step = 1)} class="btn-ghost text-xs flex items-center gap-1">
							<svg xmlns="http://www.w3.org/2000/svg" class="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
								<polyline points="15 18 9 12 15 6"/>
							</svg>
							뒤로
						</button>
						<div class="flex items-center gap-2">
							<button onclick={onClose} class="btn-ghost text-xs">취소</button>
							<button
								onclick={handleStep2}
								disabled={!selectedServer}
								class="btn text-xs bg-red-500/15 text-red-400/80 border border-red-500/20
								       hover:bg-red-500/20 hover:text-red-400
								       disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
							>
								삭제하기
							</button>
						</div>
					</div>

				{:else if step === 3}
					<!-- ── Step 3: Confirmation ── -->
					{#if selectedServer}
						<div class="bg-white/3 border border-white/8 rounded-lg px-4 py-3 mb-4">
							<p class="text-sm font-semibold text-white/85">{selectedServer.server_name}</p>
							<p class="text-[10px] text-white/30 font-mono mt-0.5">
								{selectedServer.host}
								<span class="mx-1.5 text-white/15">·</span>
								{selectedServer.network === 'internal' ? '내부망' : '외부망'}
							</p>
						</div>
					{/if}

					<p class="text-xs text-white/40 mb-2">이 작업은 되돌릴 수 없습니다.</p>

					{#if deleteError}
						<p class="text-xs text-red-400/80 mt-3 mb-1">{deleteError}</p>
					{/if}

					<div class="flex items-center justify-between mt-5">
						<button
							onclick={() => (step = 2)}
							class="btn-ghost text-xs"
							disabled={deleting}
						>
							취소
						</button>
						<button
							onclick={handleDelete}
							disabled={deleting}
							class="btn text-xs bg-red-500/80 text-white hover:bg-red-500/90
							       disabled:opacity-50 disabled:cursor-not-allowed
							       flex items-center gap-1.5 transition-colors"
						>
							{#if deleting}
								<span class="w-3 h-3 border border-white/30 border-t-white rounded-full animate-spin"></span>
								삭제 중...
							{:else}
								삭제
							{/if}
						</button>
					</div>
				{/if}
			</div>
		</div>
	</div>
{/if}

<style>
	/* modal-card animation is defined in app.css (@keyframes modal-in) */
</style>
